<template>
  <div v-loading="loading">
    <el-page-header :content="`飞行计划 ${plan?.planNo || ''}`" @back="$router.back()"
      class="ts-card" style="padding: 12px 16px">
      <template #content>
        <span class="num">飞行计划 {{ plan?.planNo }}</span>
        <el-tag v-if="plan" :type="PLAN_STATUS_MAP[plan.status]?.type" effect="light" style="margin-left: 10px">
          {{ plan.statusName }}
        </el-tag>
      </template>
    </el-page-header>

    <el-row :gutter="12" style="margin-top: 12px">
      <el-col :span="15">
        <div class="ts-card">
          <div class="card-head">
            <span class="card-title">任务航线</span>
            <el-tag v-if="plan?.status === 3" type="primary" effect="dark" size="small">● 实时跟踪</el-tag>
          </div>
          <div v-if="mapError" class="map-fallback">
            <el-alert :title="mapError" type="warning" :closable="false" />
          </div>
          <div v-show="!mapError" ref="mapEl" class="map-box" />
        </div>

        <div class="ts-card" style="margin-top: 12px" v-if="plan?.status === 3 || plan?.currentLng">
          <div class="card-head"><span class="card-title">实时位置（后台仿真器沿航线推进，2s 刷新）</span></div>
          <div class="live-grid">
            <div><label>经度</label><b class="num">{{ plan?.currentLng ?? '—' }}</b></div>
            <div><label>纬度</label><b class="num">{{ plan?.currentLat ?? '—' }}</b></div>
            <div><label>高度</label><b class="num">{{ plan?.currentAltitude ?? '—' }} m</b></div>
            <div><label>地速</label><b class="num">{{ plan?.currentSpeed ?? '—' }} m/s</b></div>
            <div><label>电量</label><b class="num" :class="{ low: (plan?.currentBattery ?? 100) < 20 }">
              {{ plan?.currentBattery ?? '—' }}%</b></div>
            <div><label>最后上报</label><b class="num" style="font-size: 12px">{{ plan?.lastReportAt || '—' }}</b></div>
          </div>
        </div>
      </el-col>

      <el-col :span="9">
        <div class="ts-card">
          <div class="card-head"><span class="card-title">方案信息</span></div>
          <el-descriptions :column="1" border size="small" style="margin-top: 10px">
            <el-descriptions-item label="关联订单">
              <router-link :to="`/admin/order/${plan?.orderId}`" class="num">{{ plan?.orderNo }}</router-link>
            </el-descriptions-item>
            <el-descriptions-item label="任务名称">{{ plan?.taskName || '—' }}</el-descriptions-item>
            <el-descriptions-item label="飞行器">{{ plan?.aircraftNo }} {{ plan?.aircraftModel }}</el-descriptions-item>
            <el-descriptions-item label="机长">{{ plan?.pilotName }}</el-descriptions-item>
            <el-descriptions-item label="起降场">
              {{ plan?.takeoffVertiportName }} → {{ plan?.landingVertiportName }}
            </el-descriptions-item>
            <el-descriptions-item label="航程 / 航时">
              <span class="num">{{ fmtDistance(plan?.distanceM) }} / {{ fmtDuration(plan?.durationSec) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="高度 / 巡航">
              <span class="num">{{ plan?.altitudeM }}m / {{ plan?.cruiseSpeed }}m/s</span>
            </el-descriptions-item>
            <el-descriptions-item label="计划起飞">{{ plan?.planStartAt || '—' }}</el-descriptions-item>
            <el-descriptions-item v-if="plan?.rejectReason" label="驳回原因">
              <span class="danger">{{ plan.rejectReason }}</span>
            </el-descriptions-item>
          </el-descriptions>

          <div class="weather ts-card" v-if="plan?.weather">
            <div class="w-head">
              <span>申报时气象（Mock）</span>
              <el-tag size="small" :type="plan.weather.flyable ? 'success' : 'danger'">
                {{ plan.weather.flyable ? '适飞' : '不适飞' }}
              </el-tag>
            </div>
            <div class="w-grid">
              <span>{{ plan.weather.weatherDesc }}</span>
              <span>{{ plan.weather.windDirectionName }}风 {{ plan.weather.windSpeed }}m/s</span>
              <span>能见度 {{ (plan.weather.visibilityM / 1000).toFixed(1) }}km</span>
              <span>{{ plan.weather.temperature }}℃</span>
            </div>
          </div>

          <div class="actions">
            <el-button v-if="plan?.status === 0 && userStore.hasPerm('dispatch:submit')"
              type="warning" @click="submit">提交申报</el-button>
            <template v-if="plan?.status === 1 && userStore.hasPerm('approval:handle')">
              <el-button type="success" @click="approve">审批通过</el-button>
              <el-button type="danger" @click="reject">驳回</el-button>
            </template>
            <el-button v-if="plan?.status === 2 && !plan?.assignedAt && userStore.hasPerm('dispatch:assign')"
              type="primary" @click="assign">下发机长</el-button>
            <el-button v-if="canExecute && plan?.status === 2 && plan?.assignedAt"
              type="success" size="large" @click="takeoff">一键起飞</el-button>
            <el-button v-if="canExecute && plan?.status === 3"
              type="warning" size="large" @click="land">确认降落送达</el-button>
            <el-button v-if="plan && [0, 1, 2].includes(plan.status) && userStore.hasPerm('dispatch:plan')"
              @click="cancel">取消计划</el-button>
            <el-button v-if="plan?.status === 5 && userStore.hasPerm('dispatch:plan')"
              @click="replan">重新出方案</el-button>
          </div>
        </div>

        <div class="ts-card" style="margin-top: 12px">
          <div class="card-head"><span class="card-title">审批与执行记录</span></div>
          <el-timeline style="margin-top: 12px">
            <el-timeline-item v-for="(t, i) in timeline" :key="i" :type="t.type" :timestamp="t.time">
              {{ t.text }}
            </el-timeline-item>
          </el-timeline>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getPlanApi, submitPlanApi, approvePlanApi, rejectPlanApi, assignPlanApi,
  takeoffPlanApi, landPlanApi, cancelPlanApi, listAirspacesApi, getResourcesApi,
  type FlightPlanVO, type AirspaceVO,
} from '@/api/dispatch'
import { PLAN_STATUS_MAP, fmtDistance, fmtDuration } from '@/constants/dict'
import { createDispatchMap, amapError, type NamedPoint } from '@/composables/useAmap'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const id = Number(route.params.id)
const plan = ref<FlightPlanVO | null>(null)
const airspaces = ref<AirspaceVO[]>([])
const loading = ref(true)
const mapEl = ref<HTMLElement | null>(null)
const mapError = ref('')
let mapInst: { destroy: () => void; setLive: (lng: number, lat: number) => void } | null = null
let timer: number | null = null

const canExecute = computed(() =>
  userStore.hasPerm('dispatch:execute') && plan.value?.pilotId === userStore.userInfo?.id)

const timeline = computed(() => {
  if (!plan.value) return []
  const p = plan.value
  const items: { text: string; time: string; type: 'primary' | 'success' | 'danger' | 'info' }[] = []
  items.push({ text: '方案已保存（待申报）', time: p.createdAt, type: 'info' })
  if (p.submittedAt) items.push({ text: '已提交空管申报', time: p.submittedAt, type: 'primary' })
  for (const a of p.approvals || []) {
    items.push({
      text: `${a.approverName || '空管'}：${a.result === 1 ? '审批通过' : '驳回'}${a.opinion ? `（${a.opinion}）` : ''}`,
      time: a.createdAt, type: a.result === 1 ? 'success' : 'danger',
    })
  }
  if (p.assignedAt) items.push({ text: `已下发机长 ${p.pilotName}（GCS Mock）`, time: p.assignedAt, type: 'primary' })
  if (p.actualTakeoffAt) items.push({ text: '无人机已起飞', time: p.actualTakeoffAt, type: 'primary' })
  if (p.actualLandingAt) items.push({ text: '已降落送达', time: p.actualLandingAt, type: 'success' })
  return items
})

async function load(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    plan.value = await getPlanApi(id)
    await renderMap()
  } finally {
    loading.value = false
  }
}

async function renderMap() {
  if (!mapEl.value || !plan.value) return
  const routePts = plan.value.route || []
  const markers: Record<string, NamedPoint> = {}
  // 场站坐标优先用计划 VO 自带（机长无资源接口权限也能显示）；缺失时再查资源表
  const p = plan.value
  if (p.takeoffVertiportLng != null && p.takeoffVertiportLat != null) {
    markers.takeoff = { lng: p.takeoffVertiportLng, lat: p.takeoffVertiportLat, name: p.takeoffVertiportName || '起飞场' }
  }
  if (p.landingVertiportLng != null && p.landingVertiportLat != null) {
    markers.landing = { lng: p.landingVertiportLng, lat: p.landingVertiportLat, name: p.landingVertiportName || '降落场' }
  }
  if (!markers.takeoff || !markers.landing) {
    try {
      const res = await getResourcesApi()
      const byName = new Map(res.vertiports.map((v) => [v.stationName, v]))
      const t = byName.get(p.takeoffVertiportName || '')
      const l = byName.get(p.landingVertiportName || '')
      if (!markers.takeoff && t) markers.takeoff = { lng: t.lng, lat: t.lat, name: t.stationName }
      if (!markers.landing && l) markers.landing = { lng: l.lng, lat: l.lat, name: l.stationName }
    } catch { /* 资源失败不阻塞 */ }
  }
  if (p.order) {
    markers.sender = { lng: p.order.senderLng, lat: p.order.senderLat, name: p.order.senderAddress }
    markers.receiver = { lng: p.order.receiverLng, lat: p.order.receiverLat, name: p.order.receiverAddress }
  }
  if (p.currentLng != null && p.currentLat != null) {
    markers.live = { lng: p.currentLng, lat: p.currentLat }
  }
  try {
    mapInst?.destroy()
    mapInst = await createDispatchMap(mapEl.value, {
      airspaces: airspaces.value, route: routePts, routeReturn: p.routeReturn || [], markers, fit: 'route',
    })
    if (p.currentLng != null && p.currentLat != null) {
      mapInst.setLive(p.currentLng, p.currentLat)
    }
  } catch {
    mapError.value = amapError.value || '地图加载失败'
  }
}

async function submit() {
  await ElMessageBox.confirm('确认提交空管申报？', '提交申报', { type: 'info' })
  const ack = await submitPlanApi(id)
  ElMessage.success(`申报成功，申报号 ${ack.applyNo || ''}`)
  load()
}
async function approve() {
  const { value } = await ElMessageBox.prompt('审批意见（可选）', '审批通过', {
    confirmButtonText: '确认通过', inputValue: '符合低空飞行要求，同意',
  })
  await approvePlanApi(id, value || undefined)
  ElMessage.success('已审批通过')
  load()
}
async function reject() {
  const { value } = await ElMessageBox.prompt('驳回原因（必填，将退回调度方修改）', '驳回计划', {
    confirmButtonText: '确认驳回', confirmButtonClass: 'el-button--danger',
    inputValidator: (v) => (v && v.trim() ? true : '驳回原因必填'),
  })
  await rejectPlanApi(id, value!)
  ElMessage.success('已驳回')
  load()
}
async function assign() {
  await ElMessageBox.confirm(`确认下发给机长 ${plan.value?.pilotName}？`, '下发机长', { type: 'warning' })
  const ack = await assignPlanApi(id)
  ElMessage.success(`已下发，指令号 ${ack.commandNo || ''}`)
  load()
}
async function takeoff() {
  await ElMessageBox.confirm('请确认地面检查已完成、周边净空，确认起飞？', '起飞确认', {
    type: 'warning', confirmButtonText: '确认起飞',
  })
  await takeoffPlanApi(id)
  ElMessage.success('无人机已起飞，进入实时跟踪')
  await load()
  startPolling()
}
async function land() {
  await ElMessageBox.confirm('确认无人机已降落、货物已送达？', '降落确认', {
    type: 'warning', confirmButtonText: '确认降落送达',
  })
  await landPlanApi(id)
  ElMessage.success('已降落送达，等待客户确认收货')
  stopPolling()
  load()
}
async function cancel() {
  const { value } = await ElMessageBox.prompt('取消原因', '取消计划', { inputValue: '运营调整' })
  await cancelPlanApi(id, value || undefined)
  ElMessage.success('计划已取消')
  load()
}
function replan() {
  if (plan.value) router.push({ path: '/admin/dispatch/board', query: { orderId: String(plan.value.orderId) } })
}

function startPolling() {
  stopPolling()
  timer = window.setInterval(async () => {
    if (!mapInst) return
    const fresh = await getPlanApi(id)
    plan.value = fresh
    if (fresh.currentLng != null && fresh.currentLat != null) mapInst.setLive(fresh.currentLng, fresh.currentLat)
    if (fresh.status !== 3) stopPolling()
  }, 2000)
}
function stopPolling() {
  if (timer) { window.clearInterval(timer); timer = null }
}

onMounted(async () => {
  try {
    airspaces.value = (await listAirspacesApi()).list
  } catch { /* ignore */ }
  await load()
  if (plan.value?.status === 3) startPolling()
})
onBeforeUnmount(() => { stopPolling(); mapInst?.destroy() })
</script>

<style lang="scss" scoped>
.card-head { display: flex; align-items: center; justify-content: space-between; }
.card-title { font-weight: 700; color: var(--ts-primary-dark); font-size: 15px; }
.map-box { width: 100%; height: 420px; border-radius: 4px; overflow: hidden; margin-top: 10px; }
.map-fallback { margin-top: 10px; }
.live-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 10px; }
.live-grid div { background: var(--ts-bg); border: 1px solid var(--ts-border); border-radius: 4px; padding: 8px; text-align: center; }
.live-grid label { display: block; font-size: 11px; color: var(--ts-text-secondary); }
.live-grid b { font-size: 14px; color: var(--ts-primary-dark); }
.live-grid b.low { color: var(--el-color-danger); }
.weather { padding: 10px; background: var(--ts-bg); margin-top: 10px; }
.w-head { display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; color: var(--ts-primary-dark); }
.w-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; font-size: 12px; color: var(--ts-text-regular); margin-top: 6px; }
.actions { margin-top: 14px; display: flex; gap: 8px; flex-wrap: wrap; }
.danger { color: var(--el-color-danger); }
</style>
