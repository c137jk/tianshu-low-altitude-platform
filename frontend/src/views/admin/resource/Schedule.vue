<template>
  <div class="ts-card pad">
    <div class="bar">
      <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" size="small"
        start-placeholder="开始日期" end-placeholder="结束日期" :clearable="false" style="width: 250px" />
      <el-radio-group v-model="shiftFilter" size="small">
        <el-radio-button :value="0">全部班次</el-radio-button>
        <el-radio-button v-for="s in SHIFT_TYPES" :key="s.value" :value="s.value">{{ s.label }}</el-radio-button>
      </el-radio-group>
      <div class="spacer" />
      <span class="muted">共 {{ filtered.length }} 条 · 人员 {{ peopleCount }} 人</span>
      <el-tag v-if="!canManage" size="small" type="info" effect="plain">只读</el-tag>
      <el-button size="small" @click="load">刷新</el-button>
      <el-button v-if="canManage" type="primary" size="small" @click="openCreate">新增排班</el-button>
    </div>

    <!-- 班次分布：一眼看出今天是不是只有早班（以前排班页只有早班一条，看不出是没数据还是没得选） -->
    <div class="dist">
      <div v-for="s in SHIFT_TYPES" :key="s.value" class="dist-cell">
        <label>{{ s.label }}</label>
        <b class="num">{{ counts[s.value] || 0 }}</b>
        <span class="muted">{{ s.start }}–{{ s.end }}{{ s.cross ? '（跨零点）' : '' }}</span>
      </div>
    </div>

    <el-table :data="filtered" v-loading="loading" stripe size="small">
      <el-table-column label="日期" width="120">
        <template #default="{ row }"><span class="num">{{ row.shift_date }}</span></template>
      </el-table-column>
      <el-table-column label="人员" width="150">
        <template #default="{ row }">
          <div class="nm">{{ row.user_name || '—' }}</div>
          <div class="muted">{{ merchantOf(row) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="班次" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="SHIFT_TYPES.find(s => s.value === row.shift_type)?.tag || 'info'" effect="light">
            {{ SHIFT_NAME[row.shift_type] || row.shift_type }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="值班时段" min-width="200">
        <template #default="{ row }">
          <span class="num">{{ fmtMin(row.start_time) }} → {{ fmtMin(row.end_time) }}</span>
          <span v-if="crossDay(row)" class="muted"> （次日）</span>
        </template>
      </el-table-column>
      <el-table-column prop="responsible_area" label="责任区域" min-width="140" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="SCHEDULE_STATUS_MAP[row.status]?.type || 'info'" effect="plain">
            {{ SCHEDULE_STATUS_MAP[row.status]?.name || row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="canManage" label="操作" width="80">
        <template #default="{ row }">
          <el-button link type="danger" size="small" @click="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !filtered.length" description="该日期范围内没有排班" :image-size="70" />

    <p class="muted note">
      班次口径来自 <code>staff_schedule.shift_type</code>：1 早班 / 2 中班 / 3 晚班 / 4 全天，
      晚班 23:00 到次日 07:00，结束时间落在第二天。同一人同一天同一班次只能排一条，重复保存会提示冲突。
      排班下发后会给对方发站内信。
    </p>

    <el-dialog v-model="show" title="新增排班" width="480px">
      <el-form :model="form" label-width="84px">
        <el-form-item label="人员">
          <el-select v-model="form.userId" filterable style="width: 100%" placeholder="机长 / 运维岗">
            <el-option v-for="p in people" :key="p.userId"
              :label="`${p.realName || p.username}（${p.roleNames || '人员'}）`" :value="p.userId" />
          </el-select>
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker v-model="form.date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="班次">
          <el-radio-group v-model="form.shiftType">
            <el-radio-button v-for="s in SHIFT_TYPES" :key="s.value" :value="s.value">{{ s.label }}</el-radio-button>
          </el-radio-group>
          <div class="muted">{{ shiftHint }}</div>
        </el-form-item>
        <el-form-item label="责任区域">
          <el-input v-model="form.area" placeholder="如：瓯江口示范区 / 温州全域" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="show = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 排班管理（需求 4.2.3「人员排班与考勤」）。
 * 以前它只是「人员管理」页里的一个页签，两条菜单指向同一个组件，切菜单页签也不跳，
 * 看着就像两个菜单没做区分；这里拆成独立页面，人员管理只留台账与资质。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
import { useUserStore } from '@/stores/user'
import { listPersonnelApi, type PersonnelVO } from '@/api/resource'
import { SHIFT_NAME, SHIFT_TYPES, SCHEDULE_STATUS_MAP, shiftRange } from '@/constants/dict'
import { plusDays, today } from '@/utils/date'

const route = useRoute()
const userStore = useUserStore()
/**
 * 看与改是两件事：`resource:schedule:view`（空管、财务）进得来这一页但改不动。
 * 真正的门在后端（POST/DELETE /aux/schedules 只认 resource:schedule），这里的显隐
 * 只是让人一眼看清自己能做什么——和 Personnel.vue 的 canAudit 同一个套路。
 */
const canManage = computed(() => userStore.hasPerm('resource:schedule'))
const rows = ref<any[]>([])
const loading = ref(true)
const saving = ref(false)
const show = ref(false)
const people = ref<PersonnelVO[]>([])
const shiftFilter = ref(0)
// 默认窗口用本地日期（见 utils/date），toISOString 会让凌晨 0~8 点整段错一天
const todayStr = today()
const range = ref<[string, string]>([todayStr, plusDays(todayStr, 6)])

const form = reactive<any>({ userId: null, date: todayStr, shiftType: 1, area: '' })

const filtered = computed(() => rows.value.filter((r) => !shiftFilter.value || r.shift_type === shiftFilter.value))
const counts = computed(() => {
  const c: Record<number, number> = {}
  for (const r of rows.value) c[r.shift_type] = (c[r.shift_type] || 0) + 1
  return c
})
const peopleCount = computed(() => new Set(rows.value.map((r) => r.user_id)).size)
const shiftHint = computed(() => {
  const s = SHIFT_TYPES.find((x) => x.value === form.shiftType)
  if (!s) return ''
  return `${s.start} → ${s.end}${s.cross ? '（次日）' : ''}`
})

function fmtMin(v: string | null) {
  return v ? String(v).slice(11, 16) : '—'
}
// staff_schedule.merchant_id 是 NOT NULL，平台侧人员（merchant 为空）落库时被填成 1，
// 直接读这个字段会把运维显示成"商家 1"。按花名册里的人来标归属。
function merchantOf(row: any) {
  const p = people.value.find((x) => x.userId === row.user_id)
  if (p) return p.merchantName || '平台'
  return row.merchant_id ? `商家 ${row.merchant_id}` : '平台'
}
function crossDay(row: any) {
  return String(row.start_time).slice(0, 10) !== String(row.end_time).slice(0, 10)
}

async function load() {
  loading.value = true
  try {
    rows.value = await request(`/aux/schedules?from=${range.value[0]}&to=${range.value[1]}`)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  show.value = true
}

async function submit() {
  if (!form.userId) return ElMessage.warning('请选择被排班人员')
  if (!form.date) return ElMessage.warning('请选择排班日期')
  const h = shiftRange(form.date, form.shiftType)
  if (!h) return ElMessage.warning('班次不合法')
  saving.value = true
  try {
    await request('/aux/schedules', {
      method: 'POST',
      data: { userId: form.userId, date: form.date, shiftType: form.shiftType, area: form.area, ...h },
    })
    ElMessage.success(`已排入${SHIFT_NAME[form.shiftType]}`)
    show.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function del(row: any) {
  const go = await ElMessageBox.confirm(
    `确认删除 ${row.user_name || ''} ${row.shift_date} 的${SHIFT_NAME[row.shift_type] || ''}排班？`,
    '删除排班', { type: 'warning' }).catch(() => null)
  if (!go) return
  await request(`/aux/schedules/${row.id}`, { method: 'DELETE' })
  ElMessage.success('已删除')
  await load()
}

function applyPreset() {
  // 从人员台账「去排班」跳过来：带着这个人把弹窗直接打开，省一次下拉。
  // 侧边栏是 keep-alive 的，再点一次不会走 onMounted，所以查 query 得挂在 watch 上。
  const preset = Number(route.query.userId)
  if (!preset || !people.value.some((p) => p.userId === preset)) return
  form.userId = preset
  show.value = true
}

watch(() => route.query.userId, applyPreset)

onMounted(async () => {
  people.value = (await listPersonnelApi()).list
  if (people.value.length && !form.userId) form.userId = people.value[0].userId
  applyPreset()
  await load()
})
</script>

<style lang="scss" scoped>
.pad { padding: 10px 14px 14px; }
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.spacer { flex: 1; }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.nm { font-size: 13px; font-weight: 600; color: var(--ts-primary-dark); }
.dist { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; }
.dist-cell {
  background: var(--ts-bg); border: 1px solid var(--ts-border); border-radius: 4px;
  padding: 8px 10px; display: flex; flex-direction: column;
}
.dist-cell label { font-size: 12px; color: var(--ts-primary-dark); font-weight: 600; }
.dist-cell b { font-size: 18px; color: var(--ts-gold); }
.num { font-family: monospace; }
.note { margin: 12px 0 0; line-height: 1.7; }
</style>
