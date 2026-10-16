<template>
  <div class="ts-card pad">
    <el-tabs v-model="tab">
      <!-- 需求 4.2.3 人员管理：台账 + 任务记录统计 + 资质到期预警 -->
      <el-tab-pane label="人员台账" name="staff">
        <div class="bar">
          <el-input v-model="keyword" size="small" clearable placeholder="姓名 / 账号 / 角色" style="width: 210px" />
          <el-checkbox v-model="onlyWarn" size="small">只看预警（资质临期 / 有过期证）</el-checkbox>
          <div class="spacer" />
          <span class="muted">资质到期预警阈值：{{ warnDays }} 天 · 共 {{ people.length }} 人</span>
          <el-button size="small" @click="loadPeople">刷新</el-button>
        </div>
        <el-table :data="filteredPeople" v-loading="loading" stripe size="small">
          <el-table-column label="姓名 / 账号" min-width="150">
            <template #default="{ row }">
              <div class="nm">{{ row.realName || row.username }}</div>
              <div class="muted num">{{ row.username }} · {{ row.merchantName || '平台' }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="roleNames" label="角色" width="150" />
          <el-table-column label="账号状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.accountStatus === 1 ? 'success' : 'danger'" effect="plain">
                {{ row.accountStatus === 1 ? '正常' : '已冻结' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="任务记录" width="200">
            <template #default="{ row }">
              <div class="num">{{ row.taskTotal }} 架次 · 完成 {{ row.taskDone }} · 在飞 {{ row.flying }}</div>
              <div class="muted num">实际飞行 {{ row.flownHours }} 小时</div>
            </template>
          </el-table-column>
          <el-table-column label="资质" width="180">
            <template #default="{ row }">
              <div>
                <span class="num">{{ row.qualTotal }} 项</span>
                <el-tag v-if="row.qualExpired" size="small" type="danger" effect="plain" style="margin-left:6px">
                  过期 {{ row.qualExpired }}
                </el-tag>
                <el-tag v-if="row.qualExpiring" size="small" type="warning" effect="plain" style="margin-left:6px">
                  临期
                </el-tag>
              </div>
              <div class="muted num">最近到期 {{ row.nearestExpireAt || '—' }}</div>
            </template>
          </el-table-column>
          <el-table-column label="今日排班" width="120">
            <template #default="{ row }">
              <el-tag size="small" :type="row.onDutyToday ? 'primary' : 'info'" effect="plain">
                {{ row.onDutyToday ? row.shiftName || '已排班' : '未排班' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="showQuals(row)">看资质</el-button>
              <el-button v-if="can('resource:schedule')" link type="primary" size="small" @click="goSchedule(row)">
                去排班
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!loading && !filteredPeople.length" description="没有符合条件的人员" :image-size="70" />
        <p class="muted note">
          花名册只列机长与运维岗（pilot / merchant_maintainer / ops_maintainer）。
          飞行小时按实际起降时间统计，不用申报时长；资质预警按系统参数 staff_qual_warn_days 计算。
        </p>
      </el-tab-pane>

      <!-- 需求 4.2.3 资质管理：人员 / 飞行器 / 商家三类主体的证照台账与复审。
           只有排班权限的岗位也看得到这一块（他们要判断这个人能不能排），复审按钮另说 -->
      <el-tab-pane label="资质台账" name="qual">
        <div class="bar">
          <el-radio-group v-model="subjectType" size="small" @change="loadQuals">
            <el-radio-button :value="0">全部</el-radio-button>
            <el-radio-button :value="2">人员</el-radio-button>
            <el-radio-button :value="3">飞行器</el-radio-button>
            <el-radio-button :value="1">商家</el-radio-button>
          </el-radio-group>
          <el-checkbox v-model="onlyPending" size="small">
            {{ onlyPending ? `只看待复审（${pendingCount}）` : `待复审 ${pendingCount} 件` }}
          </el-checkbox>
          <div class="spacer" />
          <el-button v-if="canAudit" type="primary" size="small" @click="openCreate">新增资质</el-button>
          <el-tag v-if="!canAudit" size="small" type="info" effect="plain">只读</el-tag>
          <span class="muted">{{ canAudit ? '这里登记的新件也停在「待复审」，复核通过才算有效'
            : '这里只能查看；复审由资质审核岗处理，驳回意见会回送到主体所属商家' }}</span>
        </div>
        <el-table :data="shownQuals" v-loading="qLoading" stripe size="small">
          <el-table-column label="主体" min-width="150">
            <template #default="{ row }">
              <div class="nm">{{ row.subjectName }}</div>
              <div class="muted">{{ row.subjectTypeName }} · {{ row.merchantName || '平台' }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="qualName" label="资质名称" min-width="200" />
          <el-table-column label="类别" width="110">
            <template #default="{ row }">{{ QUAL_TYPE_NAME[row.qualType] || row.qualType }}</template>
          </el-table-column>
          <el-table-column prop="issueAt" label="签发" width="100" class-name="num" />
          <el-table-column prop="expireAt" label="到期日" width="100" class-name="num" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="QUAL_STATUS_MAP[row.status]?.type || 'info'">{{ row.statusName }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="复审留痕" min-width="160">
            <template #default="{ row }">
              <span v-if="row.auditedAt" class="muted">{{ row.auditedAt }}</span>
              <div v-if="row.auditOpinion" class="muted">{{ row.auditOpinion }}</div>
              <span v-if="!row.auditedAt && !row.auditOpinion" class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="190">
            <template #default="{ row }">
              <el-button v-if="row.fileUrl" link type="primary" size="small" @click="openQualFile(row)">
                原件
              </el-button>
              <template v-if="canAudit && row.status !== 1">
                <el-button link type="success" size="small" @click="audit(row, 1)">通过</el-button>
                <el-button link type="danger" size="small" @click="audit(row, 4)">驳回</el-button>
              </template>
              <span v-else-if="row.status === 1" class="muted">有效</span>
              <span v-else class="muted">待审核岗处理</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!qLoading && !shownQuals.length" :description="onlyPending ? '没有待复审的新件' : '没有符合条件的资质'" :image-size="70" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="qualShow" :title="`${curName} 的资质`" width="660px">
      <el-table :data="curQuals" size="small" stripe>
        <el-table-column label="资质名称" min-width="200">
          <template #default="{ row }">
            <div>{{ row.qualName }}</div>
            <div class="muted">{{ QUAL_TYPE_NAME[row.qualType] || row.qualType }}</div>
            <div v-if="row.auditOpinion" class="muted">{{ row.auditOpinion }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="issueAt" label="签发" width="100" class-name="num" />
        <el-table-column prop="expireAt" label="到期" width="100" class-name="num" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="QUAL_STATUS_MAP[row.status]?.type || 'info'" effect="plain">
              {{ row.statusName }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="原件" width="80">
          <template #default="{ row }">
            <el-button v-if="row.fileUrl" link type="primary" size="small" @click="openQualFile(row)">查看</el-button>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!curQuals.length" description="该人员暂无资质记录" :image-size="60" />
    </el-dialog>

    <!-- 代登记：商家是组织、飞行器是机器，这两类主体没有"本人"，只能由审核岗代登记。
         代登记不等于复核——提交后同样停在待复审。 -->
    <el-dialog v-model="createShow" title="登记资质（提交后停在待复审）" width="560px">
      <el-form :model="form" label-width="92px" size="small">
        <el-form-item label="主体类型">
          <el-radio-group v-model="form.subjectType" @change="onSubjectChange">
            <el-radio-button :value="2">人员</el-radio-button>
            <el-radio-button :value="3">飞行器</el-radio-button>
            <el-radio-button :value="1">商家</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="资质主体">
          <!-- 商家账号只能给自己登记（后端按 merchant_scope 判），下拉就没必要摆出来 -->
          <el-input v-if="form.subjectType === 1 && !isPlatform" :model-value="ownMerchant" disabled />
          <el-select v-else v-model="form.subjectId" filterable placeholder="选择主体" style="width: 100%">
            <el-option v-for="o in subjectOptions" :key="o.id" :label="o.label" :value="o.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="资质类别">
          <!-- 类别跟着主体走：后端会拒"给机长登记营业执照"，这里就别给这个选项 -->
          <el-select v-model="form.qualType" style="width: 100%" @change="onQualType">
            <el-option v-for="t in qualTypesOf(form.subjectType)" :key="t.value"
                       :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="资质名称">
          <el-input v-model="form.qualName" maxlength="64" placeholder="如：民用无人机操控员执照" />
        </el-form-item>
        <el-form-item label="签发日期">
          <el-date-picker v-model="form.issueAt" type="date" value-format="YYYY-MM-DD"
                          placeholder="可空" style="width: 100%" />
        </el-form-item>
        <el-form-item label="到期日期" required>
          <el-date-picker v-model="form.expireAt" type="date" value-format="YYYY-MM-DD"
                          placeholder="到期预警按这一天扫描，必填" style="width: 100%" />
        </el-form-item>
        <el-form-item label="证照原件" required>
          <el-upload ref="uploadRef" :auto-upload="false" :limit="1"
                     accept=".pdf,.jpg,.jpeg,.png,.webp" :show-file-list="false"
                     :on-change="onFileChange" :on-exceed="onExceed">
            <el-button size="small">{{ picked ? `已选：${picked.name}` : '选择 PDF / 图片' }}</el-button>
          </el-upload>
          <span class="muted">原件落在服务端受控目录，复审时从列表里的「原件」按钮读，没有直链</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createShow = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">提交复审</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, genFileId } from 'element-plus'
import type { UploadFile, UploadInstance, UploadRawFile } from 'element-plus'
import { useUserStore } from '@/stores/user'
import {
  auditQualificationApi, createQualificationApi, listAircraftApi, listMerchantsApi,
  listPersonnelApi, listQualificationsApi, openQualFileApi,
  type AircraftVO, type PersonnelVO, type QualVO,
} from '@/api/resource'
import { QUAL_STATUS_MAP, QUAL_TYPE_NAME, qualTypesOf } from '@/constants/dict'

const userStore = useUserStore()
const can = (c: string) => userStore.hasPerm(c)
/**
 * 读与审是两件事：这一页谁能打开由 router/menu 的 ['resource:qualification','resource:schedule']
 * 决定，后端 GET /qualifications 必须放行这份名单的超集（现在还多了 system:user 与
 * resource:aircraft），否则就像运维那样一进页面就吃 403。这条对齐关系由
 * tests/test_qual_ledger_view.py 钉住，所以这里不再重复写一遍显隐条件。
 * canAudit 只管"复审 / 新增"这两个动作，它仍然只认 resource:qualification。
 */
const canAudit = computed(() => can('resource:qualification'))
const router = useRouter()

// 本页只有「人员台账 + 资质台账」，排班归 /admin/resource/schedule 独立页面管
const tab = ref('staff')

const people = ref<PersonnelVO[]>([])
const quals = ref<QualVO[]>([])
const warnDays = ref(15)
const loading = ref(true)
const qLoading = ref(false)
const keyword = ref('')
const onlyWarn = ref(false)
const subjectType = ref(0)
const onlyPending = ref(false)

const filteredPeople = computed(() => people.value.filter((p) => {
  if (onlyWarn.value && !(p.qualExpiring || p.qualExpired)) return false
  const k = keyword.value.trim()
  if (!k) return true
  return [p.realName || '', p.username, p.roleNames || '', p.merchantName || ''].some((s) => s.includes(k))
}))

/** 待复审的件数直接顶在筛选框上：这是这一页当下真正要办的活，别让人自己数 */
const pendingCount = computed(() => quals.value.filter((q) => q.status === 0).length)
const shownQuals = computed(() =>
  onlyPending.value ? quals.value.filter((q) => q.status === 0) : quals.value)

async function loadPeople() {
  loading.value = true
  try {
    const r = await listPersonnelApi()
    people.value = r.list
    warnDays.value = r.qualWarnDays
  } finally { loading.value = false }
}

async function loadQuals() {
  qLoading.value = true
  try {
    quals.value = (await listQualificationsApi(subjectType.value || undefined)).list
  } finally { qLoading.value = false }
}

// ---------------- 资质明细 / 复审 / 原件 ----------------
const qualShow = ref(false)
const curName = ref('')
const curQuals = ref<QualVO[]>([])
async function showQuals(row: PersonnelVO) {
  curName.value = row.realName || row.username
  // subject_type=2 时 subject_id 就是 users.id，按 id 对齐比按姓名匹配可靠
  let pool = quals.value.filter((q) => q.subjectType === 2)
  if (!pool.length) pool = (await listQualificationsApi(2)).list
  curQuals.value = pool.filter((q) => q.subjectId === row.userId)
  qualShow.value = true
}

const openQualFile = (row: QualVO) => openQualFileApi(row.id, row.qualName)

async function audit(q: QualVO, result: 1 | 4) {
  let opinion = ''
  if (result === 4) {
    const r = await ElMessageBox.prompt('驳回原因（会站内信回送给主体所属商家）', `驳回「${q.qualName}」`, {
      inputPlaceholder: '如：证件照片模糊，无法核对有效期',
    }).catch(() => null)
    if (!r) return
    opinion = String(r.value || '')
  } else {
    const go = await ElMessageBox.confirm(
      `确认「${q.subjectName} · ${q.qualName}」复核通过并置为有效？`, '资质复审', { type: 'warning' })
      .catch(() => null)
    if (!go) return
  }
  await auditQualificationApi(q.id, result, opinion)
  ElMessage.success(result === 1 ? '已通过，资质置为有效' : '已驳回')
  await Promise.all([loadQuals(), loadPeople()])
}

// ---------------- 代登记新资质 ----------------
const isPlatform = computed(() => userStore.isPlatform)
const ownMerchant = computed(() => userStore.userInfo?.merchantName || '本商家')
const createShow = ref(false)
const submitting = ref(false)
const uploadRef = ref<UploadInstance>()
const picked = ref<UploadRawFile | null>(null)
const aircrafts = ref<AircraftVO[]>([])
const merchants = ref<{ id: number; name: string }[]>([])
const form = reactive({
  subjectType: 2,
  subjectId: undefined as number | undefined,
  qualType: 'driver',
  qualName: '',
  issueAt: '',
  expireAt: '',
})

const subjectOptions = computed(() => {
  if (form.subjectType === 2) {
    return people.value.map((p) => ({ id: p.userId, label: `${p.realName || p.username} · ${p.username}` }))
  }
  if (form.subjectType === 3) {
    return aircrafts.value.map((a) => ({
      id: a.id,
      label: `${a.aircraftNo} · ${a.modelName || a.model}${a.merchantName ? ` · ${a.merchantName}` : ''}`,
    }))
  }
  return merchants.value.map((m) => ({ id: m.id, label: m.name }))
})

/**
 * 主体下拉按需拉：飞行器与商家清单都要 resource:aircraft，
 * 而这页的只读岗位看不到「新增资质」按钮——不该为了一个不会点的按钮去打别人的权限点。
 */
async function ensureSubjects(t: number) {
  try {
    if (t === 3 && !aircrafts.value.length) aircrafts.value = (await listAircraftApi()).list
    if (t === 1 && isPlatform.value && !merchants.value.length) merchants.value = await listMerchantsApi()
  } catch { /* 拦截器已经提示过，这里只让下拉留空 */ }
}

function onSubjectChange() {
  form.subjectId = undefined
  const allow = qualTypesOf(form.subjectType)
  if (!allow.some((t) => t.value === form.qualType)) {
    form.qualType = allow[0].value
    form.qualName = allow[0].label
  }
  ensureSubjects(form.subjectType)
}

function openCreate() {
  createShow.value = true
  onSubjectChange()
  form.expireAt = ''
  picked.value = null
  uploadRef.value?.clearFiles()
}

function onQualType() {
  // 名称没动过就跟着类别走，动过了别抢人家用户上的正式证照名
  if (!form.qualName.trim() || Object.values(QUAL_TYPE_NAME).includes(form.qualName)) {
    form.qualName = QUAL_TYPE_NAME[form.qualType] || ''
  }
}

function onFileChange(file: UploadFile) {
  picked.value = (file.raw as UploadRawFile) || null
}

/** limit=1 时的替换选择：不清空的话第二次选文件会被 el-upload 直接丢掉 */
function onExceed(files: File[]) {
  uploadRef.value?.clearFiles()
  const f = files[0] as UploadRawFile
  f.uid = genFileId()
  uploadRef.value?.handleStart(f)
  picked.value = f
}

async function submitCreate() {
  if (!form.subjectId) return ElMessage.warning('请选择资质主体')
  if (!form.expireAt) return ElMessage.warning('到期日期必填：到期预警按它扫描')
  if (form.issueAt && form.expireAt <= form.issueAt) return ElMessage.warning('到期日期需晚于签发日期')
  if (!picked.value) return ElMessage.warning('请选择证照原件')

  const fd = new FormData()
  fd.append('subjectType', String(form.subjectType))
  fd.append('subjectId', String(form.subjectId))
  fd.append('qualType', form.qualType)
  fd.append('qualName', form.qualName.trim() || QUAL_TYPE_NAME[form.qualType])
  if (form.issueAt) fd.append('issueAt', form.issueAt)
  fd.append('expireAt', form.expireAt)
  fd.append('file', picked.value, picked.value.name)

  submitting.value = true
  try {
    await createQualificationApi(fd)
    ElMessage.success('已登记，等待复审确认后生效')
    createShow.value = false
    form.subjectId = undefined
    form.expireAt = ''
    picked.value = null
    uploadRef.value?.clearFiles()
    if (form.subjectType === 2) await loadPeople()
    await loadQuals()
  } finally { submitting.value = false }
}

// ---------------- 跳转排班 ----------------
// 这里以前是个「排今天」按钮，写死 shiftType=1 直接插当天早班：台账里只能排出早班，
// 而且排班入口散在两个页面。写操作统一收到排班管理页，四种班次都在那儿选。
function goSchedule(row: PersonnelVO) {
  router.push({ path: '/admin/resource/schedule', query: { userId: String(row.userId) } })
}

onMounted(async () => {
  await Promise.all([loadPeople(), loadQuals()])
})
</script>

<style lang="scss" scoped>
.pad { padding: 10px 14px 14px; }
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.spacer { flex: 1; }
.nm { font-size: 13px; font-weight: 600; color: var(--ts-primary-dark); }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.note { margin: 10px 0 0; line-height: 1.6; }
</style>
