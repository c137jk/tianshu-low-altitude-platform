<template>
  <div class="ts-card" v-loading="loading">
    <div class="card-head">
      <b>设备在线</b>
      <el-radio-group v-model="stateFilter" size="small">
        <el-radio-button :value="''">全部 {{ rows.length }}</el-radio-button>
        <el-radio-button v-for="s in STATES" :key="s" :value="s">
          {{ STATE_NAME[s] }} {{ counts[s] || 0 }}
        </el-radio-button>
      </el-radio-group>
      <el-select v-model="typeFilter" size="small" style="width:120px" clearable placeholder="全部类别">
        <el-option :value="1" label="机载终端" />
        <el-option :value="2" label="地面设备" />
      </el-select>
      <div class="spacer" />
      <span class="muted">心跳阈值 {{ timeoutSec }} 秒 · 统计于 {{ ts }}</span>
      <el-switch v-model="auto" size="small" active-text="自动刷新" style="margin:0 8px" />
      <el-button size="small" @click="load">刷新</el-button>
    </div>

    <p class="muted note">
      机载终端的在线/离线来自遥测心跳（仿真在飞时每 tick 回写一次位置与上报时间）；
      飞行计划在跑但心跳断了即判「失联」，停场无遥测判「离线」。
      地面设备上报过心跳即同样按心跳阈值判定；从未上报过的（终端还没接进来）状态取场站运营状态，
      时间那一栏带「· 资料更新」后缀，说的是资料变更时间，不是心跳。
    </p>

    <el-table :data="filtered" stripe size="small" style="margin-top:6px">
      <el-table-column label="类别" width="90">
        <template #default="{ row }">{{ row.typeName }}</template>
      </el-table-column>
      <el-table-column label="编号" width="130">
        <template #default="{ row }"><b class="num">{{ row.no }}</b></template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="150" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tooltip v-if="row.lostReason" :content="`${row.lostReason}（${row.lostBy || '—'} 标记于 ${row.lostAt}）`">
            <el-tag size="small" :type="TAG_TYPE[row.state]">{{ row.stateName }}</el-tag>
          </el-tooltip>
          <el-tag v-else size="small" :type="TAG_TYPE[row.state]">{{ row.stateName }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="电量" width="130">
        <template #default="{ row }">
          <!-- 带 status 的 el-progress 会把百分比换成状态图标，所以读数单独写出来 -->
          <div v-if="row.terminalType === 1" class="bat">
            <el-progress class="bar" :percentage="Math.round(row.battery ?? 0)" :stroke-width="8"
              :show-text="false"
              :status="row.battery <= (threshold || 20) ? 'exception' : row.battery < 50 ? 'warning' : 'success'" />
            <span class="num" :class="{ low: row.battery <= (threshold || 20) }">{{ Math.round(row.battery ?? 0) }}%</span>
          </div>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="在飞任务" min-width="150">
        <template #default="{ row }">
          <span v-if="row.planNo" class="num">{{ row.planNo }}</span>
          <span v-else-if="row.terminalType === 2" class="muted">
            桩位 {{ row.chargerAvailable }}/{{ row.chargerTotal }} 可用 · 占用 {{ row.occupiedCount }}
          </span>
          <span v-else class="muted">{{ row.statusName }}</span>
        </template>
      </el-table-column>
      <el-table-column label="最后心跳 / 更新" width="180">
        <template #default="{ row }">
          <div class="num">{{ row.lastHeartbeat || '—' }}</div>
          <div class="muted tiny">{{ ago(row.heartbeatAgeSec) }}{{ row.heartbeatKind === 'record' ? ' · 资料更新' : '' }}</div>
        </template>
      </el-table-column>
      <el-table-column label="位置" width="90">
        <template #default="{ row }">
          <el-button v-if="row.lng != null" link type="primary" size="small" @click="openMap(row)">
            看图
          </el-button>
          <span v-else class="muted">无坐标</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <!-- 只有存在人工标记时才给"解除"：系统按心跳判出的失联，删标记也解不掉 -->
          <el-button v-if="row.lostAt" link size="small" @click="unmark(row)">解除失联</el-button>
          <el-button v-else link type="danger" size="small" @click="openMark(row)">标记失联</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="markShow" title="标记失联" width="440px">
      <p class="muted">
        「{{ cur?.no }}」当前由系统判定的状态是 {{ cur?.stateName }}。
        标记失联用于库里推不出来、但现场已确认的情况（如图传丢失、只回传遥测不回传位置）。
      </p>
      <el-input v-model="reason" type="textarea" :rows="3" maxlength="200" show-word-limit
        placeholder="如：地面站图传丢失，仅链路有遥测，按失联处置" />
      <template #footer>
        <el-button size="small" @click="markShow = false">取消</el-button>
        <el-button size="small" type="danger" :loading="busy" @click="doMark">确认标记</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="mapShow" :title="`${cur?.no || ''} 最近位置`" width="min(760px, 94vw)"
               @opened="renderMap" @closed="destroyMap">
      <div ref="mapEl" class="pick-map" />
      <p class="muted tiny" v-if="cur">
        经纬度 {{ cur.lng?.toFixed(6) }}, {{ cur.lat?.toFixed(6) }}（GCJ-02）·
        采集于 {{ cur.lastHeartbeat }}（{{ ago(cur.heartbeatAgeSec) }}）
      </p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'
import { loadAmap } from '@/composables/useAmap'

interface DeviceRow {
  terminalType: number; typeName: string; id: number; no: string; name: string
  state: 'online' | 'offline' | 'lost'; stateName: string
  battery?: number; onlineFlag?: number; statusName?: string; planNo?: string | null
  chargerTotal?: number; chargerAvailable?: number; occupiedCount?: number
  lastHeartbeat: string | null; heartbeatAgeSec: number | null; heartbeatKind: string
  lng: number | null; lat: number | null
  lostReason: string | null; lostBy: string | null; lostAt: string | null
}

const STATES = ['lost', 'offline', 'online'] as const
const STATE_NAME: Record<string, string> = { lost: '失联', offline: '离线', online: '在线' }
const TAG_TYPE: Record<string, any> = { lost: 'danger', offline: 'info', online: 'success' }

const rows = ref<DeviceRow[]>([])
const counts = ref<Record<string, number>>({})
const timeoutSec = ref(60)
const threshold = ref(20)
const ts = ref('—')
const loading = ref(true)
const busy = ref(false)
const auto = ref(true)
const stateFilter = ref<'' | typeof STATES[number]>('')
const typeFilter = ref<number | ''>('')
const timer = ref<number>(0)

const filtered = computed(() => rows.value.filter(r => {
  if (stateFilter.value && r.state !== stateFilter.value) return false
  if (typeFilter.value !== '' && r.terminalType !== typeFilter.value) return false
  return true
}))

function ago(sec: number | null) {
  if (sec === null) return '从未上报'
  if (sec < 60) return `${sec} 秒前`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`
  return `${Math.floor(sec / 86400)} 天前`
}

async function load(quiet = false) {
  if (!quiet) loading.value = true
  try {
    const d: any = await request('/monitor/devices')
    rows.value = d.list
    counts.value = d.counts
    timeoutSec.value = d.timeoutSec
    threshold.value = d.batteryThreshold
    ts.value = d.ts
  } finally {
    if (!quiet) loading.value = false
  }
}

const cur = ref<DeviceRow | null>(null)
const markShow = ref(false)
const reason = ref('')
function openMark(row: DeviceRow) {
  cur.value = row; reason.value = ''; markShow.value = true
}
async function doMark() {
  if (!reason.value.trim()) return ElMessage.warning('请填写失联原因，便于后续追溯')
  busy.value = true
  try {
    await request('/monitor/devices/lost-mark', {
      method: 'POST',
      data: { terminalType: cur.value!.terminalType, deviceId: cur.value!.id, reason: reason.value.trim() },
    })
    ElMessage.success('已标记为失联')
    markShow.value = false
    await load(true)
  } finally { busy.value = false }
}
async function unmark(row: DeviceRow) {
  await request('/monitor/devices/lost-mark', {
    method: 'DELETE', params: { terminalType: row.terminalType, deviceId: row.id },
  })
  ElMessage.success('已解除失联标记，状态回到按遥测判定')
  await load(true)
}

const mapShow = ref(false)
const mapEl = ref<HTMLElement | null>(null)
let map: any = null
function openMap(row: DeviceRow) { cur.value = row; mapShow.value = true }
async function renderMap() {
  if (!mapEl.value || cur.value?.lng == null) return
  try {
    const AMap = await loadAmap()
    map = new AMap.Map(mapEl.value, { zoom: 14, center: [cur.value.lng, cur.value.lat], viewMode: '2D' })
    new AMap.Marker({
      position: [cur.value.lng, cur.value.lat], map,
      content: `<div style="width:16px;height:16px;border-radius:50%;background:${
        cur.value.state === 'lost' ? '#C0392B' : '#23406E'};border:3px solid #fff;box-shadow:0 0 8px rgba(0,0,0,.4)"></div>`,
      offset: new AMap.Pixel(-8, -8),
    })
  } catch { ElMessage.warning('地图加载失败，可参考下方经纬度') }
}
function destroyMap() { map?.destroy(); map = null }

watch(auto, (on) => {
  window.clearInterval(timer.value)
  if (on) timer.value = window.setInterval(() => load(true), 5000)
})
onMounted(() => { load(); if (auto.value) timer.value = window.setInterval(() => load(true), 5000) })
onBeforeUnmount(() => window.clearInterval(timer.value))
</script>

<style scoped>
.card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.spacer { flex: 1; }
.muted { color: #909399; }
.tiny { font-size: 12px; }
.note { margin: 8px 0 0; font-size: 12px; line-height: 1.6; }
.bat { display: flex; align-items: center; gap: 6px; }
.bar { flex: 1; min-width: 56px; }
.num.low { color: #c0392b; font-weight: 700; }
.pick-map { height: 360px; border: 1px solid #e2e5ee; border-radius: 4px; }
</style>
