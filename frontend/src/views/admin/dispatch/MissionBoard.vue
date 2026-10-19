<template>
  <div class="mission-board">
    <!-- 左：我的任务 -->
    <div class="ts-card col-left">
      <div class="col-head">
        <span class="card-title">我的飞行任务</span>
        <el-button text :icon="Refresh" @click="reload" />
      </div>
      <el-radio-group v-model="tab" size="small" style="margin: 10px 0" @change="reload">
        <el-radio-button :value="3">执行中</el-radio-button>
        <el-radio-button :value="2">待起飞</el-radio-button>
        <el-radio-button :value="''">全部</el-radio-button>
      </el-radio-group>
      <div class="mission-list">
        <div
          v-for="p in list"
          :key="p.id"
          class="mission-card"
          :class="{ active: current?.id === p.id }"
          @click="select(p)"
        >
          <div class="mc-top">
            <span class="num mc-no">{{ p.planNo }}</span>
            <el-tag :type="PLAN_STATUS_MAP[p.status]?.type" size="small" effect="light">{{ p.statusName }}</el-tag>
          </div>
          <div class="mc-task">{{ p.taskName }}</div>
          <div class="mc-route">
            <span>{{ p.takeoffVertiportName }}</span>
            <el-icon><Right /></el-icon>
            <span>{{ p.landingVertiportName }}</span>
          </div>
          <div class="mc-meta">
            {{ p.aircraftNo }} · <span class="num">{{ fmtDistance(p.distanceM) }} / {{ fmtDuration(p.durationSec) }}</span>
          </div>
          <div v-if="p.status === 3" class="mc-live">
            电量 <b class="num" :class="{ low: (p.currentBattery ?? 100) < 20 }">{{ p.currentBattery ?? '—' }}%</b>
          </div>
        </div>
        <el-empty v-if="!list.length" description="暂无任务" :image-size="80" />
      </div>
    </div>

    <!-- 右：任务窗 -->
    <div class="ts-card col-right" v-loading="loading">
      <div v-if="!current" class="hint">
        <el-icon class="hint-icon"><Position /></el-icon>
        <p>选择左侧任务查看航线与执行操作</p>
      </div>
      <template v-else>
        <div class="col-head">
          <span class="card-title">{{ current.taskName }}</span>
          <el-tag :type="PLAN_STATUS_MAP[current.status]?.type" effect="light">{{ current.statusName }}</el-tag>
        </div>

        <div v-if="mapError" class="map-fallback" style="margin-top: 10px">
          <el-alert :title="mapError" type="warning" :closable="false" />
        </div>
        <div v-show="!mapError" ref="mapEl" class="map-box" />

        <el-descriptions :column="2" border size="small" style="margin-top: 12px">
          <el-descriptions-item label="订单号"><span class="num">{{ current.orderNo }}</span></el-descriptions-item>
          <el-descriptions-item label="飞行器">{{ current.aircraftNo }}</el-descriptions-item>
          <el-descriptions-item label="起飞场">{{ current.takeoffVertiportName }}</el-descriptions-item>
          <el-descriptions-item label="降落场">{{ current.landingVertiportName }}</el-descriptions-item>
          <el-descriptions-item label="航程"><span class="num">{{ fmtDistance(current.distanceM) }}</span></el-descriptions-item>
          <el-descriptions-item label="预计航时"><span class="num">{{ fmtDuration(current.durationSec) }}</span></el-descriptions-item>
          <el-descriptions-item label="飞行高度"><span class="num">{{ current.altitudeM }}m</span></el-descriptions-item>
          <el-descriptions-item label="巡航速度"><span class="num">{{ current.cruiseSpeed }}m/s</span></el-descriptions-item>
        </el-descriptions>

        <div v-if="current.status === 3" class="live-panel">
          <div class="live-item"><label>当前电量</label>
            <b class="num" :class="{ low: (current.currentBattery ?? 100) < 20 }">{{ current.currentBattery ?? '—' }}%</b></div>
          <div class="live-item"><label>地速</label><b class="num">{{ current.currentSpeed ?? '—' }} m/s</b></div>
          <div class="live-item"><label>高度</label><b class="num">{{ current.currentAltitude ?? '—' }} m</b></div>
          <div class="live-item"><label>最后上报</label><b class="num" style="font-size: 12px">{{ current.lastReportAt || '—' }}</b></div>
        </div>

        <el-alert v-if="current.status === 2 && !current.assignedAt" type="info" :closable="false"
          title="方案已批准但尚未下发，请等待调度员下发 GCS 任务指令。" style="margin-top: 12px" />

        <div class="actions">
          <el-button
            v-if="current.status === 2 && current.assignedAt"
            type="success" size="large" :loading="acting" @click="takeoff"
          >一键起飞</el-button>
          <el-button
            v-if="current.status === 3"
            type="warning" size="large" :loading="acting" @click="land"
          >确认降落送达</el-button>
          <el-button @click="$router.push(`/admin/dispatch/plans/${current.id}`)">完整详情</el-button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Right, Position } from '@element-plus/icons-vue'
import {
  listPlansApi, getPlanApi, takeoffPlanApi, landPlanApi,
  listAirspacesApi, type FlightPlanVO, type AirspaceVO,
} from '@/api/dispatch'
import { PLAN_STATUS_MAP, fmtDistance, fmtDuration } from '@/constants/dict'
import { createDispatchMap, amapError, type NamedPoint } from '@/composables/useAmap'

const list = ref<FlightPlanVO[]>([])
const current = ref<FlightPlanVO | null>(null)
// 默认「全部」：新下发的任务状态是待起飞，停在「执行中」页签上会看不到，机长会以为没派单
const tab = ref<number | ''>('')
const loading = ref(false)
const acting = ref(false)
const airspaces = ref<AirspaceVO[]>([])
const mapEl = ref<HTMLElement | null>(null)
const mapError = ref('')
let mapInst: { destroy: () => void; setLive: (lng: number, lat: number) => void } | null = null
let timer: number | null = null

async function reload() {
  loading.value = true
  try {
    const res = await listPlansApi({
      page: 1, pageSize: 50, mine: true,
      status: tab.value === '' ? '' : tab.value,
    })
    list.value = res.list
    const flying = list.value.find((p) => p.status === 3)
    if (flying) {
      await select(flying)
    } else if (!current.value || !list.value.some((p) => p.id === current.value!.id)) {
      if (list.value[0]) await select(list.value[0])
    } else {
      // 当前任务仍在列表中但状态已变化（如刚降落），刷新右侧详情
      const fresh = list.value.find((p) => p.id === current.value!.id)
      if (fresh && fresh.status !== current.value.status) await select(fresh)
    }
  } finally {
    loading.value = false
  }
}

async function select(p: FlightPlanVO) {
  current.value = await getPlanApi(p.id)
  mapError.value = ''
  await renderMap()
  if (current.value.status === 3) startPolling()
  else stopPolling()
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
  if (p.currentLng != null && p.currentLat != null) {
    markers.live = { lng: p.currentLng, lat: p.currentLat }
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

async function takeoff() {
  if (!current.value) return
  await ElMessageBox.confirm('起飞前检查：电量充足、桨叶无异物、起降场净空、航线气象适飞。确认起飞？',
    '起飞前检查', { type: 'warning', confirmButtonText: '检查完毕，起飞' })
  acting.value = true
  try {
    await takeoffPlanApi(current.value.id)
    ElMessage.success('已起飞，祝飞行顺利')
    tab.value = 3
    await reload()
    startPolling()
  } finally {
    acting.value = false
  }
}

async function land() {
  if (!current.value) return
  await ElMessageBox.confirm('确认无人机已在降落场安全降落、货物已交付？', '降落确认', {
    type: 'warning', confirmButtonText: '确认降落送达',
  })
  acting.value = true
  try {
    await landPlanApi(current.value.id)
    ElMessage.success('已确认降落送达')
    stopPolling()
    tab.value = ''
    await reload()
  } finally {
    acting.value = false
  }
}

function startPolling() {
  stopPolling()
  timer = window.setInterval(async () => {
    if (!current.value || !mapInst) return
    const fresh = await getPlanApi(current.value.id)
    current.value = fresh
    if (fresh.currentLng != null && fresh.currentLat != null) mapInst.setLive(fresh.currentLng, fresh.currentLat)
    if (fresh.status !== 3) { stopPolling(); reload() }
  }, 2000)
}
function stopPolling() {
  if (timer) { window.clearInterval(timer); timer = null }
}

onMounted(async () => {
  try { airspaces.value = (await listAirspacesApi()).list } catch { /* ignore */ }
  await reload()
})
onBeforeUnmount(() => { stopPolling(); mapInst?.destroy() })
</script>

<style lang="scss" scoped>
.mission-board { display: grid; grid-template-columns: 380px 1fr; gap: 12px; height: calc(100vh - 56px - 32px); }
.ts-card { display: flex; flex-direction: column; overflow: hidden; padding: 12px; min-width: 0; }
.col-head { display: flex; align-items: center; justify-content: space-between; }
.card-title { font-weight: 700; color: var(--ts-primary-dark); font-size: 15px; }
.mission-list { overflow-y: auto; flex: 1; }
.mission-card {
  border: 1px solid var(--ts-border); border-radius: 6px; padding: 10px 12px;
  margin-bottom: 10px; cursor: pointer; background: #fff;
}
.mission-card:hover { border-color: var(--ts-gold); }
.mission-card.active { border-color: var(--ts-gold); background: rgba(200, 164, 92, .08); box-shadow: inset 3px 0 0 var(--ts-gold); }
.mc-top { display: flex; justify-content: space-between; align-items: center; }
.mc-no { font-size: 12px; color: var(--ts-primary); }
.mc-task { font-weight: 600; margin: 6px 0; }
.mc-route { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--ts-text-regular); }
.mc-route .el-icon { color: var(--ts-gold); }
.mc-meta { font-size: 12px; color: var(--ts-text-secondary); margin-top: 4px; }
.mc-live { margin-top: 6px; font-size: 12px; color: var(--ts-text-secondary); }
.map-box { height: 380px; border-radius: 4px; overflow: hidden; margin-top: 10px; flex: none; }
.live-panel { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 12px; }
.live-item { background: var(--ts-bg); border: 1px solid var(--ts-border); border-radius: 4px; padding: 10px; text-align: center; }
.live-item label { display: block; font-size: 11px; color: var(--ts-text-secondary); }
.live-item b { font-size: 15px; color: var(--ts-primary-dark); }
.low { color: var(--el-color-danger) !important; }
.actions { margin-top: 14px; display: flex; gap: 10px; }
.hint { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--ts-text-secondary); }
.hint-icon { font-size: 42px; color: var(--ts-gold); margin-bottom: 10px; }
</style>
