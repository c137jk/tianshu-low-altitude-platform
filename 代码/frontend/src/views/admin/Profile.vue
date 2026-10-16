<template>
  <div class="wrap">
    <el-card>
      <template #header><b>个人中心</b></template>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="姓名">{{ us.userInfo?.realName }}</el-descriptions-item>
        <el-descriptions-item label="账号">{{ us.userInfo?.username }}</el-descriptions-item>
        <el-descriptions-item label="所属商家">{{ us.userInfo?.merchantName || '平台方' }}</el-descriptions-item>
        <el-descriptions-item label="角色">{{ (us.userInfo?.roles || []).join(', ') }}</el-descriptions-item>
      </el-descriptions>
      <el-divider />
      <el-form :model="pwd" label-width="80px" size="small" style="max-width:400px">
        <el-form-item label="原密码"><el-input v-model="pwd.old" type="password" show-password /></el-form-item>
        <el-form-item label="新密码"><el-input v-model="pwd.pwd" type="password" show-password /></el-form-item>
        <el-form-item label="确认密码"><el-input v-model="pwd.pwd2" type="password" show-password /></el-form-item>
        <el-button size="small" type="primary" :loading="saving" @click="save">修改密码</el-button>
      </el-form>
    </el-card>

    <!-- 我的资质与值班：资质到期预警发给本人，这里就是本人查得到自己那份的地方。
         不挂任何 resource:* 权限点，机长/空管/财务登录后都能看到，只看到自己的 -->
    <el-card v-if="isStaff" class="duty">
      <template #header>
        <div class="hd">
          <b>我的资质与值班</b>
          <span class="muted">{{ duty?.range.from }} ~ {{ duty?.range.to }} · 预警阈值 {{ duty?.qualWarnDays ?? '—' }} 天</span>
          <el-button link size="small" @click="load">刷新</el-button>
        </div>
      </template>

      <div v-loading="loading">
        <div class="cells">
          <div class="cell">
            <label>今日排班</label>
            <b>{{ duty?.todayOnDuty ? '在岗' : '休息' }}</b>
          </div>
          <div class="cell">
            <label>近 7 天班次</label>
            <b class="num">{{ duty?.shifts.length ?? 0 }}</b>
          </div>
          <div class="cell">
            <label>今天全区在岗</label>
            <b class="num">{{ duty?.todayOnDutyTotal ?? 0 }}</b>
            <span class="muted">{{ (duty?.dutyBoard || []).map(b => `${b.shiftName}${b.count}`).join(' / ') }}</span>
          </div>
          <div class="cell">
            <label>我的资质</label>
            <b class="num">{{ quals.length }}</b>
            <span v-if="expiredCount" class="muted danger">{{ expiredCount }} 项已过期</span>
            <span v-else-if="warnCount" class="muted warn">{{ warnCount }} 项临期</span>
          </div>
        </div>

        <div class="bar">
          <b class="blk">我的资质</b>
          <span class="muted">新递的证件停在「待审核」，复审通过才算有效；被驳回的会带上意见</span>
          <div class="spacer" />
          <el-button type="primary" size="small" @click="openUpload">上传新证件</el-button>
        </div>
        <el-table :data="quals" size="small" stripe>
          <el-table-column label="资质名称" min-width="200">
            <template #default="{ row }">
              <div>{{ row.qualName }}</div>
              <div class="muted">{{ QUAL_TYPE_NAME[row.qualType] || row.qualType }}</div>
              <div v-if="row.auditOpinion" class="muted danger">驳回意见：{{ row.auditOpinion }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="issueAt" label="签发" width="100" class-name="num" />
          <el-table-column label="到期" width="150" class-name="num">
            <template #default="{ row }">
              {{ row.expireAt || '—' }}
              <span v-if="row.daysLeft !== null" class="muted">（{{ expiryText(row) }}）</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="QUAL_STATUS_MAP[row.status]?.type || 'info'" effect="plain">
                {{ row.statusName }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="原件" width="80">
            <template #default="{ row }">
              <el-button v-if="row.fileUrl" link type="primary" size="small" @click="openFile(row)">查看</el-button>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!quals.length" description="系统里还没有你的资质记录" :image-size="54" />

        <el-table :data="duty?.shifts || []" size="small" stripe class="shifts">
          <el-table-column prop="shiftDate" label="日期" width="110" class-name="num" />
          <el-table-column label="班次" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="SHIFT_TYPES.find(s => s.value === row.shiftType)?.tag || 'info'" effect="light">
                {{ row.shiftName || row.shiftType }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="值班时段" min-width="180">
            <template #default="{ row }"><span class="num">{{ hm(row.startTime) }} → {{ hm(row.endTime) }}</span></template>
          </el-table-column>
          <el-table-column prop="area" label="责任区域" min-width="140" show-overflow-tooltip />
          <el-table-column prop="statusName" label="状态" width="80" />
        </el-table>
        <el-empty v-if="!duty?.shifts.length" description="这段时间没有你的排班" :image-size="54" />

        <p class="muted note">
          资质临期与到期由系统按 staff_qual_warn_days 每天扫描，到期前会给你发站内信；
          换证直接在这里上传新证件的原件，提交后停在「待审核」，由资质审核岗复核放行——
          本人不能把自己递的件直接置为有效。
        </p>
      </div>
    </el-card>

    <!-- 自助上传：主体由服务端写死成"本人"，这里连填别人 id 的入口都不给 -->
    <el-dialog v-model="uploadShow" title="上传我的新证件" width="540px">
      <el-form :model="form" label-width="92px" size="small">
        <el-form-item label="资质类别">
          <!-- 自助口只登记"人员资质"（主体写死本人），所以只给驾驶证/培训证两类 -->
          <el-select v-model="form.qualType" style="width: 100%" @change="onQualType">
            <el-option v-for="t in myQualTypes" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="证件名称">
          <el-input v-model="form.qualName" maxlength="64" placeholder="按证件原件上的名称填写" />
        </el-form-item>
        <el-form-item label="签发日期">
          <el-date-picker v-model="form.issueAt" type="date" value-format="YYYY-MM-DD"
                          placeholder="可空" style="width: 100%" />
        </el-form-item>
        <el-form-item label="有效期至" required>
          <el-date-picker v-model="form.expireAt" type="date" value-format="YYYY-MM-DD"
                          placeholder="到期预警按这一天扫描，必填" style="width: 100%" />
        </el-form-item>
        <el-form-item label="证件原件" required>
          <el-upload ref="uploadRef" :auto-upload="false" :limit="1"
                     accept=".pdf,.jpg,.jpeg,.png,.webp" :show-file-list="false"
                     :on-change="onFileChange" :on-exceed="onExceed">
            <el-button size="small">{{ picked ? `已选：${picked.name}` : '选择 PDF / 图片' }}</el-button>
          </el-upload>
          <span class="muted">拍全、别遮挡有效期。复审要看原件，只写一句话是过不了的。</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadShow = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">提交复审</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, genFileId } from 'element-plus'
import type { UploadFile, UploadInstance, UploadRawFile } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { changePasswordApi } from '@/api/auth'
import {
  myDutyApi, openMyQualFileApi, uploadMyQualApi,
  type MyDutyVO, type MyQualVO,
} from '@/api/resource'
import { QUAL_STATUS_MAP, QUAL_TYPE_NAME, SHIFT_TYPES, qualTypesOf } from '@/constants/dict'

const us = useUserStore()
const isStaff = computed(() => (us.userInfo?.userType || 0) >= 3)
const pwd = reactive({ old: '', pwd: '', pwd2: '' })
const saving = ref(false)

async function save() {
  if (!pwd.old) return ElMessage.warning('请输入原密码')
  if (pwd.pwd.length < 6) return ElMessage.warning('新密码至少 6 位')
  if (pwd.pwd !== pwd.pwd2) return ElMessage.warning('两次密码不一致')
  saving.value = true
  try {
    await changePasswordApi(pwd.old, pwd.pwd)
    ElMessage.success('密码已修改，下次登录请用新密码')
    pwd.old = pwd.pwd = pwd.pwd2 = ''
  } finally { saving.value = false }
}

const duty = ref<MyDutyVO | null>(null)
const loading = ref(false)
const quals = computed(() => duty.value?.qualifications || [])
const warnCount = computed(() => quals.value.filter((q) => q.warn).length)
const expiredCount = computed(() => quals.value.filter((q) => q.status === 3).length)

function hm(v: string | null) {
  return v ? v.slice(11, 16) : '—'
}
function expiryText(q: MyQualVO) {
  if (q.daysLeft === null) return ''
  return q.daysLeft < 0 ? `已过期 ${-q.daysLeft} 天` : `剩 ${q.daysLeft} 天`
}

async function load() {
  if (!isStaff.value) return
  loading.value = true
  try { duty.value = await myDutyApi() } finally { loading.value = false }
}

const openFile = (row: MyQualVO) => openMyQualFileApi(row.id, row.qualName)

// ---------------- 自助上传新证件 ----------------
const myQualTypes = qualTypesOf(2)          // 主体只能是本人（2），所以只有驾驶证 / 培训证
const uploadShow = ref(false)
const uploading = ref(false)
const uploadRef = ref<UploadInstance>()
const picked = ref<UploadRawFile | null>(null)
const form = reactive({ qualType: 'driver', qualName: '驾驶证', issueAt: '', expireAt: '' })

function openUpload() {
  uploadShow.value = true
  form.expireAt = ''
  picked.value = null
  uploadRef.value?.clearFiles()
}

function onQualType() {
  // 名称没动过（或还是上一个类别的默认名）就跟着类别走，动过了别抢用户填的正式证照名
  const known = Object.values(QUAL_TYPE_NAME)
  if (!form.qualName.trim() || known.includes(form.qualName)) {
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

async function submitUpload() {
  if (!form.expireAt) return ElMessage.warning('请填写有效期：到期预警按这一天扫描')
  if (form.issueAt && form.expireAt <= form.issueAt) return ElMessage.warning('有效期需晚于签发日期')
  if (!picked.value) return ElMessage.warning('请选择证件原件：复审要看原件')

  const fd = new FormData()
  fd.append('qualType', form.qualType)
  fd.append('qualName', form.qualName.trim() || QUAL_TYPE_NAME[form.qualType])
  if (form.issueAt) fd.append('issueAt', form.issueAt)
  fd.append('expireAt', form.expireAt)
  fd.append('file', picked.value, picked.value.name)

  uploading.value = true
  try {
    await uploadMyQualApi(fd)
    ElMessage.success('已提交，资质审核岗复审后生效')
    uploadShow.value = false
    form.expireAt = ''
    picked.value = null
    uploadRef.value?.clearFiles()
    await load()
  } finally { uploading.value = false }
}

onMounted(load)
</script>

<style lang="scss" scoped>
.wrap { padding: 20px; max-width: 900px; display: flex; flex-direction: column; gap: 16px; }
.hd { display: flex; align-items: center; gap: 12px; }
.cells { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; }
.cell {
  background: var(--ts-bg); border: 1px solid var(--ts-border); border-radius: 4px;
  padding: 8px 10px; display: flex; flex-direction: column;
}
.cell label { font-size: 12px; color: var(--ts-text-secondary); }
.cell b { font-size: 17px; color: var(--ts-primary-dark); }
.num { font-family: monospace; }
.bar { display: flex; align-items: center; gap: 10px; margin: 2px 0 10px; flex-wrap: wrap; }
.spacer { flex: 1; }
.blk { font-size: 13px; color: var(--ts-primary-dark); }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.warn { color: var(--ts-gold); }
.danger { color: var(--el-color-danger); }
.shifts { margin-top: 10px; }
.note { margin: 12px 0 0; line-height: 1.6; }
</style>
