<template>
  <div class="ts-card">
    <el-tabs v-model="tab">
      <el-tab-pane label="飞行器台账" name="fleet">
        <div class="card-head">
          <el-input v-model="keyword" size="small" clearable placeholder="机号 / 机型 / 场站" style="width: 200px" />
          <el-select v-model="statusFilter" size="small" clearable placeholder="全部状态" style="width: 130px">
            <el-option v-for="(n, s) in AC_STATUS" :key="s" :value="Number(s)" :label="n" />
          </el-select>
          <div class="spacer" />
          <el-button v-if="can('resource:aircraft')" type="primary" size="small" @click="openCreate">+ 新增飞行器</el-button>
          <el-button v-if="userStore.isPlatform" size="small" type="warning" @click="chargeAll">一键充换电</el-button>
          <el-tag size="small" effect="plain">共 {{ filtered.length }} 架 · 执行中 {{ flyingCount }} 架 · 低电量 {{ lowBatteryCount }} 架</el-tag>
        </div>

        <el-table :data="filtered" v-loading="loading" stripe size="default" style="margin-top: 12px">
          <el-table-column prop="aircraftNo" label="机号" width="130">
            <template #default="{ row }">
              <b class="num"><el-button link type="primary" @click="openDetail(row)">{{ row.aircraftNo }}</el-button></b>
            </template>
          </el-table-column>
          <el-table-column prop="model" label="机型" min-width="140" />
          <el-table-column v-if="userStore.isPlatform" prop="merchantName" label="所属商家" min-width="140">
            <template #default="{ row }">{{ row.merchantName || '—' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="row.status === 2 ? 'success' : row.status >= 4 ? 'danger' : 'info'" size="small">
                {{ row.statusName }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="电量" width="150">
            <template #default="{ row }">
              <!-- 带 status 的 el-progress 会把百分比换成状态图标，读数单独写出来 -->
              <div class="bat">
                <el-progress class="bar" :percentage="Math.round(row.battery)" :stroke-width="10" :show-text="false"
                  :status="row.battery < 20 ? 'exception' : row.battery < 50 ? 'warning' : 'success'" />
                <span class="num" :class="{ warn: row.battery < 20 }">{{ Math.round(row.battery) }}%</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="healthScore" label="健康分" width="90">
            <template #default="{ row }"><span class="num">{{ row.healthScore }}</span></template>
          </el-table-column>
          <el-table-column prop="taskCount" label="任务数" width="80" />
          <el-table-column prop="totalFlightMin" label="累计飞行(min)" width="120" />
          <el-table-column prop="maxLoadKg" label="载重(kg)" width="90" />
          <el-table-column prop="vertiportName" label="当前场站" min-width="150" />
          <el-table-column label="下次维保" width="115">
            <template #default="{ row }">
              <span class="num" :class="{ warn: row.nextMaintainAt && row.nextMaintainAt < today }">
                {{ row.nextMaintainAt || '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="操作" :width="can('resource:aircraft') ? 190 : 90" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
              <template v-if="can('resource:aircraft')">
                <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
                <el-dropdown trigger="click" style="margin-left:8px">
                  <el-button link size="small">改状态</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item v-for="(n, s) in AC_STATUS" :key="s" :disabled="row.status === Number(s)"
                        @click="setStatus(row, Number(s))">{{ n }}</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </template>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!loading && !filtered.length" description="没有符合条件的飞行器" :image-size="70" />
        <p class="muted note">
          「执行中」由调度与实时仿真驱动，不在手工切换范围内；其余状态商家运维可直接置位（送修、换电）。
          一键充换电是演练开关，有在飞任务时后端会拒绝。
        </p>
      </el-tab-pane>

      <el-tab-pane label="维保记录" name="mt" lazy>
        <MaintenanceTab @changed="reload" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dlg" :title="form.id ? '编辑飞行器' : '新增飞行器'" width="460px">
      <el-form :model="form" label-width="90px" size="small">
        <el-form-item v-if="!form.id" label="机号">
          <el-input v-model="form.aircraftNo" placeholder="留空自动生成" />
        </el-form-item>
        <el-form-item v-if="!form.id && userStore.isPlatform" label="所属商家">
          <el-select v-model="form.merchantId" style="width: 100%" filterable>
            <el-option v-for="m in merchants" :key="m.id" :label="m.name" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="品牌"><el-input v-model="form.brand" /></el-form-item>
        <el-form-item label="型号"><el-input v-model="form.model" /></el-form-item>
        <el-form-item label="续航(分)"><el-input-number v-model="form.enduranceMin" :min="5" :max="600" /></el-form-item>
        <el-form-item label="载重(kg)"><el-input-number v-model="form.maxLoadKg" :min="0" :max="200" :precision="1" /></el-form-item>
        <el-form-item label="限高(m)"><el-input-number v-model="form.maxAltitude" :min="30" :max="1000" /></el-form-item>
        <el-form-item v-if="!form.id" label="停放场站">
          <el-select v-model="form.vertiportId" style="width: 100%">
            <el-option v-for="v in stations" :key="v.id" :label="v.stationName" :value="v.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="dlg = false">取消</el-button>
        <el-button size="small" type="primary" :loading="busy" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailShow" :title="`${det?.aircraft.aircraftNo || ''} 详情`"
               size="min(760px, 94vw)" :with-header="true">
      <div v-loading="detLoading" class="det">
        <template v-if="det">
          <div class="det-head">
            <el-tag :type="det.aircraft.status === 2 ? 'success' : det.aircraft.status >= 4 ? 'danger' : 'info'">
              {{ det.aircraft.statusName }}
            </el-tag>
            <span>电量 <b class="num" :class="{ warn: det.aircraft.battery < 20 }">{{ det.aircraft.battery }}%</b></span>
            <span>健康分 <b class="num">{{ det.aircraft.healthScore }}</b></span>
            <span>累计飞行 <b class="num">{{ Math.round(det.aircraft.totalFlightMin / 60) }}</b> 小时</span>
            <span>故障 <b class="num">{{ det.aircraft.faultCount }}</b> 次</span>
            <span v-if="det.aircraft.maintainOverdue" class="warn">下次维保已过期（{{ det.aircraft.nextMaintainAt }}）</span>
          </div>

          <el-alert v-if="det.flying" type="warning" :closable="false" class="fly">
            <b>在飞任务 {{ det.flying.planNo }}</b>
            {{ det.flying.taskName || '—' }} · {{ det.flying.takeoffAt }} 起飞 ·
            高度 {{ det.flying.altitudeM ?? '—' }}m · 电量 {{ det.flying.battery ?? '—' }}% ·
            最后遥测 {{ det.flying.lastReportAt || '—' }}
          </el-alert>

          <el-descriptions :column="2" border size="small" class="desc">
            <el-descriptions-item label="机型">{{ det.aircraft.modelName || '—' }}</el-descriptions-item>
            <el-descriptions-item label="序列号">{{ det.aircraft.serialNo || '—' }}</el-descriptions-item>
            <el-descriptions-item label="所属商家">{{ det.aircraft.merchantName || '—' }}</el-descriptions-item>
            <el-descriptions-item label="停放场站">{{ det.aircraft.vertiportName || '—' }}</el-descriptions-item>
            <el-descriptions-item label="续航 / 载重">
              {{ det.aircraft.enduranceMin ?? '—' }} 分钟 / {{ det.aircraft.maxLoadKg }} kg
            </el-descriptions-item>
            <el-descriptions-item label="限高 / 限速">
              {{ det.aircraft.maxAltitude ?? '—' }} m / {{ det.aircraft.maxSpeed || '—' }} m/s
            </el-descriptions-item>
            <el-descriptions-item label="启用日期">{{ det.aircraft.purchaseAt || '—' }}</el-descriptions-item>
            <el-descriptions-item label="下次维保">
              <span :class="{ warn: det.aircraft.maintainOverdue }">{{ det.aircraft.nextMaintainAt || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="最近位置" :span="2">
              {{ det.aircraft.lng != null ? `${det.aircraft.lng.toFixed(6)}, ${det.aircraft.lat?.toFixed(6)}` : '—' }}
              <span class="muted">（更新于 {{ det.aircraft.updatedAt || '—' }}）</span>
            </el-descriptions-item>
          </el-descriptions>

          <div class="tiles">
            <div><span>历史任务</span><b class="num">{{ det.stats.plans }}</b></div>
            <div><span>已完成</span><b class="num">{{ det.stats.done }}</b></div>
            <div><span>驳回/取消</span><b class="num">{{ det.stats.aborted }}</b></div>
            <div><span>在办维保</span><b class="num" :class="{ warn: det.stats.maintenanceOpen }">{{ det.stats.maintenanceOpen }}</b></div>
            <div><span>未处理违规</span><b class="num" :class="{ warn: det.stats.openViolations }">{{ det.stats.openViolations }}</b></div>
          </div>

          <!-- name 带 d- 前缀：Element Plus 用 name 直接生成 DOM id（tab-mt / pane-mt），
               和外层页签同名会在同一页面上出现两个重复 id -->
          <el-tabs v-model="detTab">
            <el-tab-pane label="维保记录" name="d-mt">
              <el-table :data="det.maintenance" size="small" stripe>
                <el-table-column label="类型" width="90">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.recordType === 2 ? 'danger' : 'primary'" effect="plain">
                      {{ row.typeName }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="faultType" label="项目" width="120" />
                <el-table-column prop="description" label="描述" min-width="160" show-overflow-tooltip />
                <el-table-column prop="result" label="处理结果" min-width="140">
                  <template #default="{ row }">{{ row.result || '—' }}</template>
                </el-table-column>
                <el-table-column prop="handlerName" label="处理人" width="90" />
                <el-table-column label="登记 / 完成" width="160" class-name="num">
                  <template #default="{ row }">{{ row.startedAt || '—' }}<br>{{ row.finishedAt || '—' }}</template>
                </el-table-column>
                <el-table-column label="状态" width="80">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.status === 3 ? 'success' : 'warning'">{{ row.statusName }}</el-tag>
                  </template>
                </el-table-column>
                <template #empty><span class="muted">暂无维保记录</span></template>
              </el-table>
            </el-tab-pane>

            <el-tab-pane label="飞行履历" name="d-fl">
              <el-table :data="det.flights" size="small" stripe>
                <el-table-column prop="planNo" label="计划号" width="140" class-name="num" />
                <el-table-column prop="taskName" label="任务" min-width="130" show-overflow-tooltip />
                <el-table-column prop="route" label="航线" min-width="150" show-overflow-tooltip />
                <el-table-column label="距离" width="80" class-name="num">
                  <template #default="{ row }">{{ row.distanceKm }} km</template>
                </el-table-column>
                <el-table-column label="时长" width="110" class-name="num">
                  <template #default="{ row }">
                    {{ fmtMin(row.durationMin) }}<span v-if="!row.actual" class="muted">（申报）</span>
                  </template>
                </el-table-column>
                <el-table-column prop="takeoffAt" label="起飞" width="150" class-name="num" />
                <el-table-column label="违规" width="70" class-name="num">
                  <template #default="{ row }">
                    <span :class="{ warn: row.violations }">{{ row.violations || '—' }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="110">
                  <template #default="{ row }"><el-tag size="small" :type="row.status === 4 ? 'success' : 'info'">
                    {{ row.statusName }}</el-tag></template>
                </el-table-column>
                <template #empty><span class="muted">暂无飞行任务</span></template>
              </el-table>
            </el-tab-pane>
          </el-tabs>

          <p class="muted note">
            履历只取最近 30 条；「时长」在计划未落地时显示的是申报值，已落地的是实际起降差值，两者已标注区分。
            改状态与登记维保请到「飞行器台账」「维保记录」页签，这里只做查看。
          </p>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MaintenanceTab from './MaintenanceTab.vue'
import {
  listAircraftApi, createAircraftApi, updateAircraftApi, chargeAllAircraftApi,
  listVertiportsApi, listMerchantsApi, aircraftDetailApi,
  type AircraftVO, type AircraftDetailVO, type VertiportVO,
} from '@/api/resource'
import { useUserStore } from '@/stores/user'
import { ymd } from '@/utils/date'

// 可手工置位的状态：2（执行中）由调度链路写入，这里露出来会让人以为能手动"起飞"
const AC_STATUS: Record<number, string> = { 1: '闲置', 3: '充换电', 4: '维保中', 5: '故障离线' }

const userStore = useUserStore()
const can = (c: string) => userStore.hasPerm(c)

const tab = ref('fleet')
const list = ref<AircraftVO[]>([])
const stations = ref<VertiportVO[]>([])
const merchants = ref<{ id: number; name: string }[]>([])
const loading = ref(true)
const busy = ref(false)
const keyword = ref('')
const statusFilter = ref<number | ''>('')
// 本地日期：toISOString 走 UTC，东八区凌晨会退成昨天，维保到期提醒跟着错一天
const today = ymd(new Date())

const filtered = computed(() => list.value.filter((a) => {
  if (statusFilter.value !== '' && a.status !== statusFilter.value) return false
  const k = keyword.value.trim()
  if (!k) return true
  return [a.aircraftNo, a.model, a.vertiportName || '', a.merchantName || ''].some((s) => s.includes(k))
}))
const flyingCount = computed(() => list.value.filter((a) => a.status === 2).length)
const lowBatteryCount = computed(() => list.value.filter((a) => a.battery < 30).length)

const dlg = ref(false)
const blank = {
  id: 0, aircraftNo: '', brand: '大疆', model: 'FlyCart 30',
  enduranceMin: 30, maxLoadKg: 20, maxAltitude: 200,
}
const form = ref<any>({ ...blank })
function openCreate() {
  form.value = { ...blank, merchantId: merchants.value[0]?.id, vertiportId: stations.value[0]?.id }
  dlg.value = true
}
function openEdit(row: AircraftVO) {
  form.value = {
    id: row.id, brand: row.brand || '', model: row.modelName || row.model,
    enduranceMin: row.enduranceMin, maxLoadKg: row.maxLoadKg, maxAltitude: row.maxAltitude,
  }
  dlg.value = true
}
async function save() {
  if (!form.value.model.trim()) return ElMessage.warning('请填写型号')
  busy.value = true
  try {
    if (form.value.id) await updateAircraftApi(form.value.id, form.value)
    else await createAircraftApi(form.value)
    ElMessage.success('已保存')
    dlg.value = false
    await reload()
  } finally { busy.value = false }
}
async function setStatus(row: AircraftVO, status: number) {
  await updateAircraftApi(row.id, { status })
  ElMessage.success(`${row.aircraftNo} 已置为「${AC_STATUS[status]}」`)
  await reload()
}

const detailShow = ref(false)
const detLoading = ref(false)
const det = ref<AircraftDetailVO | null>(null)
const detTab = ref('d-mt')
// 一次任务的时长可以跨小时（在飞计划滞留到第二天落地就会有一千多分钟），只报"分"读不出量级
const fmtMin = (m: number) => (m >= 60 ? `${Math.floor(m / 60)} 小时 ${Math.round(m % 60)} 分` : `${m} 分`)
async function openDetail(row: AircraftVO) {
  det.value = null
  detTab.value = 'd-mt'
  detailShow.value = true
  detLoading.value = true
  try { det.value = await aircraftDetailApi(row.id) } finally { detLoading.value = false }
}
async function chargeAll() {
  await ElMessageBox.confirm(
    '把全场飞行器电量置为 100% 并恢复闲置？有在飞任务时后端会拒绝执行。', '一键充换电',
    { type: 'warning' })
  try {
    const d = await chargeAllAircraftApi()
    ElMessage.success(`已完成 ${d.count} 架飞行器换电`)
    await reload()
  } catch { /* 409 等业务错误由拦截器统一提示 */ }
}
async function reload() {
  list.value = (await listAircraftApi()).list
}
onMounted(async () => {
  try {
    const [ac, vp] = await Promise.all([listAircraftApi(), listVertiportsApi()])
    list.value = ac.list
    stations.value = vp.list
    if (userStore.isPlatform) merchants.value = await listMerchantsApi()
  } finally { loading.value = false }
})
</script>

<style scoped>
.card-head { display: flex; align-items: center; gap: 8px; }
.spacer { flex: 1; }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.note { margin: 10px 0 0; line-height: 1.6; }
.warn { color: #c0392b; font-weight: 700; }
.bat { display: flex; align-items: center; gap: 6px; }
.bar { flex: 1; min-width: 60px; }
.det-head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; font-size: 13px; margin-bottom: 12px; }
.fly { margin-bottom: 12px; font-size: 13px; }
.desc { margin-bottom: 12px; }
.tiles { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.tiles div { flex: 1 1 100px; border: 1px solid #e2e5ee; border-radius: 4px; padding: 8px 10px; background: #fbfcfe; }
.tiles span { display: block; font-size: 12px; color: var(--ts-text-secondary); }
.tiles b { font-size: 18px; }
</style>
