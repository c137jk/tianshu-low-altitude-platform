<template>
  <div class="board">
    <!-- 左：待审批队列 -->
    <div class="ts-card col col-left">
      <div class="col-head">
        <span class="card-title">待审批队列 <el-tag size="small" type="warning">{{ list.length }}</el-tag></span>
        <el-button text :icon="Refresh" @click="reload" />
      </div>
      <div class="batch-bar">
        <el-checkbox :model-value="allChecked" :indeterminate="indeterminate" @change="toggleAll">全选</el-checkbox>
        <el-button type="success" size="small" :disabled="!checkedIds.length" @click="batchApprove">
          批量通过（{{ checkedIds.length }}）
        </el-button>
      </div>
      <div class="queue">
        <div
          v-for="p in list"
          :key="p.id"
          class="queue-item"
          :class="{ active: current?.id === p.id }"
          @click="select(p)"
        >
          <el-checkbox :model-value="checkedIds.includes(p.id)" @change="toggleOne(p.id)" @click.stop />
          <div class="q-main">
            <div class="q-top">
              <span class="num">{{ p.planNo }}</span>
              <span class="sub">{{ p.submittedAt }}</span>
            </div>
            <div class="q-route">{{ p.taskName }}</div>
            <div class="q-meta">
              {{ p.aircraftNo }} · {{ p.pilotName }} · <span class="num">{{ fmtDistance(p.distanceM) }}</span>
            </div>
          </div>
        </div>
        <el-empty v-if="!list.length" description="暂无待审批计划" :image-size="70" />
      </div>
    </div>

    <!-- 中：航线空域叠加 -->
    <div class="ts-card col col-map">
      <div class="col-head">
        <span class="card-title">航线合规审查</span>
        <span class="legend">
          <i class="lg lg-red" />禁飞
          <i class="lg lg-yellow" />限飞
          <i class="lg lg-green" />可飞
        </span>
      </div>
      <div v-if="mapError" class="map-fallback"><el-alert :title="mapError" type="warning" :closable="false" /></div>
      <div v-show="!mapError" ref="mapEl" class="map-box" />
      <div v-if="!current" class="center-hint sub">请选择左侧待审批计划</div>
    </div>

    <!-- 右：审批面板 -->
    <div class="ts-card col col-right">
      <div class="col-head"><span class="card-title">审批意见</span></div>
      <div v-if="!current" class="hint"><p>选择计划后查看<br />航线、气象与限高校验</p></div>
      <div v-else class="panel">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="计划号"><span class="num">{{ current.planNo }}</span></el-descriptions-item>
          <el-descriptions-item label="订单号"><span class="num">{{ current.orderNo }}</span></el-descriptions-item>
          <el-descriptions-item label="航线">
            {{ current.order?.senderAddress }} → {{ current.order?.receiverAddress }}
          </el-descriptions-item>
          <el-descriptions-item label="飞行器 / 机长">
            {{ current.aircraftNo }} / {{ current.pilotName }}
          </el-descriptions-item>
          <el-descriptions-item label="飞行高度">
            <span class="num">{{ current.altitudeM }}m</span>
          </el-descriptions-item>
          <el-descriptions-item label="航程 / 航时">
            <span class="num">{{ fmtDistance(current.distanceM) }} / {{ fmtDuration(current.durationSec) }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <div class="weather ts-card" v-if="current.weather">
          <div class="w-head">
            <span>气象（Mock）</span>
            <el-tag size="small" :type="current.weather.flyable ? 'success' : 'danger'">
              {{ current.weather.flyable ? '适飞' : '不适飞' }}
            </el-tag>
          </div>
          <div class="w-grid">
            <span>{{ current.weather.weatherDesc }}</span>
            <span>{{ current.weather.windDirectionName }}风 {{ current.weather.windSpeed }}m/s</span>
            <span>能见度 {{ (current.weather.visibilityM / 1000).toFixed(1) }}km</span>
            <span>{{ current.weather.warningLevelName }}</span>
          </div>
        </div>

        <el-alert title="航线已由系统校验：未穿越禁飞/管制区，穿越限飞区已计入空域服务费与限高校验。"
          type="info" :closable="false" show-icon style="margin-bottom: 10px" />

        <el-input v-model="opinion" type="textarea" :rows="3" placeholder="审批意见（通过可选填，驳回必填原因）" />

        <div class="actions">
          <el-button type="success" :loading="acting" @click="approveOne">通过</el-button>
          <el-button type="danger" @click="rejectOne">驳回</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  listPlansApi, getPlanApi, approvePlanApi, rejectPlanApi, batchApproveApi,
  listAirspacesApi, type FlightPlanVO, type AirspaceVO,
} from '@/api/dispatch'
import { fmtDistance, fmtDuration } from '@/constants/dict'
import { createDispatchMap, amapError, type NamedPoint } from '@/composables/useAmap'

const list = ref<FlightPlanVO[]>([])
const current = ref<FlightPlanVO | null>(null)
const checkedIds = ref<number[]>([])
const opinion = ref('')
const acting = ref(false)
const airspaces = ref<AirspaceVO[]>([])
const mapEl = ref<HTMLElement | null>(null)
const mapError = ref('')
let mapInst: { destroy: () => void } | null = null

const allChecked = computed(() => list.value.length > 0 && checkedIds.value.length === list.value.length)
const indeterminate = computed(() => checkedIds.value.length > 0 && checkedIds.value.length < list.value.length)

async function reload() {
  const res = await listPlansApi({ page: 1, pageSize: 50, status: 1 })
  list.value = res.list
  checkedIds.value = checkedIds.value.filter((id) => list.value.some((p) => p.id === id))
  if (current.value && !list.value.some((p) => p.id === current.value!.id)) {
    current.value = null
    mapInst?.destroy(); mapInst = null
  }
  if (!current.value && list.value[0]) select(list.value[0])
}

function toggleAll(v: any) {
  checkedIds.value = v ? list.value.map((p) => p.id) : []
}
function toggleOne(id: number) {
  checkedIds.value = checkedIds.value.includes(id)
    ? checkedIds.value.filter((x) => x !== id)
    : [...checkedIds.value, id]
}

async function select(p: FlightPlanVO) {
  current.value = await getPlanApi(p.id)
  opinion.value = ''
  await renderMap()
}

async function renderMap() {
  if (!mapEl.value || !current.value) return
  const p = current.value
  const o = p.order
  const markers: Record<string, NamedPoint> = {}
  if (p.takeoffVertiportLng != null && p.takeoffVertiportLat != null) {
    markers.takeoff = { lng: p.takeoffVertiportLng, lat: p.takeoffVertiportLat, name: p.takeoffVertiportName || '起飞场' }
  }
  if (p.landingVertiportLng != null && p.landingVertiportLat != null) {
    markers.landing = { lng: p.landingVertiportLng, lat: p.landingVertiportLat, name: p.landingVertiportName || '降落场' }
  }
  if (o) {
    markers.sender = { lng: o.senderLng, lat: o.senderLat, name: o.senderAddress }
    markers.receiver = { lng: o.receiverLng, lat: o.receiverLat, name: o.receiverAddress }
  }
  try {
    mapInst?.destroy()
    mapInst = await createDispatchMap(mapEl.value, {
      airspaces: airspaces.value, route: p.route || [], routeReturn: p.routeReturn || [], markers, fit: 'route',
    })
  } catch {
    mapError.value = amapError.value || '地图加载失败'
  }
}

async function approveOne() {
  if (!current.value) return
  await approvePlanApi(current.value.id, opinion.value || undefined)
  ElMessage.success('已通过')
  await reload()
}

async function rejectOne() {
  if (!current.value) return
  const { value } = await ElMessageBox.prompt('驳回原因（必填）', '驳回计划', {
    confirmButtonText: '确认驳回', confirmButtonClass: 'el-button--danger',
    inputValidator: (v) => (v && v.trim() ? true : '驳回原因必填'),
  })
  await rejectPlanApi(current.value.id, value!)
  ElMessage.success('已驳回')
  await reload()
}

async function batchApprove() {
  const { value } = await ElMessageBox.prompt(
    `确认批量通过 ${checkedIds.value.length} 张计划？审批意见可选。`, '批量审批', {
      confirmButtonText: '确认全部通过', inputValue: '批量审核通过',
    })
  acting.value = true
  try {
    const res = await batchApproveApi([...checkedIds.value], value || undefined)
    ElMessage.success(`已批量通过 ${res.count} 张计划`)
    checkedIds.value = []
    await reload()
  } finally {
    acting.value = false
  }
}

onMounted(async () => {
  try { airspaces.value = (await listAirspacesApi()).list } catch { /* ignore */ }
  await reload()
})
onBeforeUnmount(() => mapInst?.destroy())
</script>

<style lang="scss" scoped>
.board { display: grid; grid-template-columns: 330px 1fr 350px; gap: 12px; height: calc(100vh - 56px - 32px); }
.col { display: flex; flex-direction: column; overflow: hidden; padding: 12px; min-width: 0; }
.col-head { display: flex; align-items: center; justify-content: space-between; }
.card-title { font-weight: 700; color: var(--ts-primary-dark); display: flex; align-items: center; gap: 6px; }
.batch-bar { display: flex; align-items: center; justify-content: space-between; margin: 10px 0 6px; }
.queue { overflow-y: auto; flex: 1; }
.queue-item {
  display: flex; gap: 8px; border: 1px solid var(--ts-border); border-radius: 5px;
  padding: 8px 10px; margin-bottom: 8px; cursor: pointer; background: #fff;
}
.queue-item:hover { border-color: var(--ts-gold); }
.queue-item.active { border-color: var(--ts-gold); background: rgba(200, 164, 92, .08); }
.q-main { flex: 1; min-width: 0; }
.q-top { display: flex; justify-content: space-between; font-size: 12.5px; color: var(--ts-primary); }
.q-route { font-size: 13px; margin: 4px 0; }
.q-meta { font-size: 12px; color: var(--ts-text-secondary); }
.map-box { flex: 1; border-radius: 4px; overflow: hidden; margin-top: 8px; min-height: 400px; }
.map-fallback { margin-top: 10px; }
.center-hint { text-align: center; padding: 40px 0; }
.legend { font-size: 12px; color: var(--ts-text-secondary); }
.lg { display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin: 0 2px 0 8px; }
.lg-red { background: rgba(192, 57, 43, .25); border: 1px solid #C0392B; }
.lg-yellow { background: rgba(211, 154, 43, .25); border: 1px solid #D39A2B; }
.lg-green { background: rgba(46, 125, 82, .25); border: 1px solid #2E7D52; }
.hint { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--ts-text-secondary); text-align: center; }
.panel { overflow-y: auto; flex: 1; margin-top: 10px; }
.weather { padding: 10px; background: var(--ts-bg); margin: 10px 0; }
.w-head { display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; color: var(--ts-primary-dark); }
.w-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; font-size: 12px; color: var(--ts-text-regular); margin-top: 6px; }
.actions { margin-top: 12px; display: flex; gap: 10px; }
.sub { color: var(--ts-text-secondary); font-size: 12px; }
</style>
