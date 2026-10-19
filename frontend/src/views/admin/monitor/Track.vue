<template>
  <el-row :gutter="12">
    <el-col :span="7">
      <el-card shadow="never" style="max-height: 76vh; overflow:auto">
        <template #header><b>历史飞行计划</b></template>
        <div v-for="t in list" :key="t.planId" class="row"
             :class="{ on: cur === t.planId }" @click="pick(t)">
          <div class="row-top">
            <b>{{ t.planNo }}</b>
            <el-tag v-if="t.status === 3" size="small" type="warning">在飞</el-tag>
            <el-tag v-if="t.violationFrames" size="small" type="danger">违规 {{ t.violationFrames }} 帧</el-tag>
          </div>
          <div style="font-size:12px;color:#667">{{ t.taskName }}</div>
          <div style="font-size:12px;color:#999">
            {{ t.startAt }} → {{ t.endAt }} · {{ t.frames }}帧 · {{ fmtDur(t.durationSec) }}
          </div>
        </div>
        <el-empty v-if="!list.length" description="暂无轨迹数据（飞完一单后自动生成）" />
      </el-card>
    </el-col>

    <el-col :span="17">
      <el-card shadow="never" body-style="padding-top:12px">
        <div class="head">
          <div>
            <b>{{ meta.planNo || '选择左侧计划开始回放' }}</b>
            <span class="muted" v-if="meta.planNo">
              {{ meta.taskName }} · {{ meta.aircraftNo }} · {{ meta.statusName }}
              · {{ meta.frames }} 帧 / {{ fmtDur(meta.durationSec) }}
            </span>
          </div>
          <div class="spacer" />
          <el-button size="small" :disabled="!points.length" @click="exportCsv">导出轨迹 CSV</el-button>
        </div>

        <div ref="mapEl" class="map" v-loading="loading">
          <div v-if="mapFail" class="map-fail">地图加载失败，可先查看下方曲线与帧表</div>
        </div>

        <div class="controls">
          <el-button :disabled="!points.length" circle @click="toggle">
            <span v-if="playing">❚❚</span><span v-else>▶</span>
          </el-button>
          <el-button size="small" :disabled="!points.length" @click="seek(0)">回起点</el-button>
          <el-select v-model="speed" size="small" style="width:88px" :disabled="!points.length">
            <el-option v-for="s in SPEEDS" :key="s" :value="s" :label="`${s}× 倍速`" />
          </el-select>
          <span class="time">{{ fmtDur(playhead) }} / {{ fmtDur(duration) }}
            <em v-if="points.length">（按真实遥测时间回放，预计还需 {{ fmtDur(Math.max(0, duration - playhead) / speed) }}）</em>
          </span>
          <div class="spacer" />
          <el-button size="small" :disabled="!vioPoints.length" @click="jumpVio(-1)">上一个违规</el-button>
          <el-button size="small" type="danger" plain :disabled="!vioPoints.length"
                     @click="jumpVio(1)">下一个违规</el-button>
        </div>

        <div class="timeline" v-if="points.length">
          <el-slider v-model="playhead" :max="duration || 1" :step="0.1" :show-tooltip="false"
                     @input="onScrub" />
          <i v-for="(p, i) in vioPoints" :key="i" class="vio-tick"
             :style="{ left: (p.offsetSec / (duration || 1)) * 100 + '%' }"
             :title="`${p.t} ${p.violationName || p.violationType || '违规'}`"
             @click="seek(p.offsetSec)" />
        </div>

        <div class="readout" v-if="curPt">
          <span>时刻 <b>{{ curPt.t }}</b></span>
          <span>位置 <b>{{ curPt.lng.toFixed(6) }}, {{ curPt.lat.toFixed(6) }}</b></span>
          <span>高度 <b>{{ curPt.altitude.toFixed(1) }} m</b></span>
          <span>速度 <b>{{ curPt.speed.toFixed(1) }} m/s</b></span>
          <span>电量 <b>{{ curPt.battery }} %</b></span>
          <span>航向 <b>{{ curPt.direction }}°</b></span>
          <span v-if="curPt.violation" class="bad">
            违规 <b>{{ curPt.violationName || curPt.violationType }}</b>
          </span>
        </div>

        <el-row :gutter="8" v-if="points.length">
          <el-col :span="14"><div ref="flightEl" class="chart" /></el-col>
          <el-col :span="10"><div ref="battEl" class="chart" /></el-col>
        </el-row>
        <el-alert v-if="meta.planNo && !points.length" type="info" :closable="false"
                  title="该计划暂无轨迹帧（未起飞或帧数上限已到）" />
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import request from '@/utils/request'
import { loadAmap } from '@/composables/useAmap'

interface TrackPoint {
  lng: number; lat: number; altitude: number; speed: number; battery: number
  direction: number; violation: number; violationType: string | null
  violationName: string | null; t: string; ts: string; offsetSec: number
}
interface TrackItem {
  planId: number; planNo: string; taskName: string; status: number; statusName: string
  frames: number; startAt: string; endAt: string; durationSec: number; violationFrames: number
}

// 倍速 = 相对真实时间的推进倍数（1× 即 1 秒现实播 1 秒遥测）。
// 演示轨迹从 60 秒到 4 小时都有，所以档位要能跨两个数量级。
const SPEEDS = [1, 2, 5, 10, 20, 50, 100]

const list = ref<TrackItem[]>([])
const points = ref<TrackPoint[]>([])
const meta = ref<any>({})
const cur = ref<number | null>(null)
const loading = ref(false)
const mapFail = ref(false)
const playhead = ref(0)
const speed = ref(5)
const playing = ref(false)

const mapEl = ref<HTMLElement | null>(null)
const flightEl = ref<HTMLElement | null>(null)
const battEl = ref<HTMLElement | null>(null)

let map: any = null
let lineDone: any = null
let lineRest: any = null
let plane: any = null
let vioMarkers: any[] = []
let fChart: any = null
let bChart: any = null
let raf = 0
let lastTs = 0

const duration = computed(() => points.value.length ? points.value[points.value.length - 1].offsetSec : 0)
const vioPoints = computed(() => points.value.filter(p => p.violation))

// 当前帧 = 最后一个 offsetSec <= 播放头的帧。曲线和读数只报真实采到的帧，
// 不做任何数值插值——位置可以插值（两点之间匀速），遥测读数插值就是编数据了。
const idx = computed(() => {
  let i = 0
  for (let k = 0; k < points.value.length; k++) {
    if (points.value[k].offsetSec <= playhead.value + 1e-6) i = k
    else break
  }
  return i
})
const curPt = computed<TrackPoint | null>(() => points.value[idx.value] || null)

function markerPos(): [number, number] {
  const pts = points.value
  const i = idx.value
  const a = pts[i]
  if (!a) return [0, 0]
  const b = pts[i + 1]
  if (!b || playhead.value >= duration.value) return [a.lng, a.lat]
  const span = b.offsetSec - a.offsetSec
  const r = span > 0 ? Math.min(1, Math.max(0, (playhead.value - a.offsetSec) / span)) : 1
  return [a.lng + (b.lng - a.lng) * r, a.lat + (b.lat - a.lat) * r]
}

function fmtDur(sec: number) {
  const s = Math.max(0, Math.round(sec))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  const left = s % 60
  const p = (n: number) => String(n).padStart(2, '0')
  return h ? `${h}:${p(m)}:${p(left)}` : `${p(m)}:${p(left)}`
}

function onScrub() { /* el-slider 已写回 playhead，地图由 watch 同步 */ }

function seek(sec: number) {
  playhead.value = Math.min(Math.max(sec, 0), duration.value)
}

function jumpVio(dir: 1 | -1) {
  const vs = vioPoints.value
  if (!vs.length) return
  if (dir === 1) {
    const next = vs.find(p => p.offsetSec > playhead.value + 0.05)
    seek(next ? next.offsetSec : vs[0].offsetSec)
  } else {
    const prev = [...vs].reverse().find(p => p.offsetSec < playhead.value - 0.05)
    seek(prev ? prev.offsetSec : vs[vs.length - 1].offsetSec)
  }
}

function toggle() {
  if (!points.value.length) return
  playing.value = !playing.value
  if (playing.value) {
    if (playhead.value >= duration.value) playhead.value = 0
    lastTs = 0
    raf = requestAnimationFrame(step)
  } else {
    cancelAnimationFrame(raf)
  }
}

function step(ts: number) {
  if (!playing.value) return
  if (lastTs) playhead.value += ((ts - lastTs) / 1000) * speed.value
  lastTs = ts
  if (playhead.value >= duration.value) {
    playhead.value = duration.value
    playing.value = false
    return
  }
  raf = requestAnimationFrame(step)
}

watch(playhead, () => drawProgress())

// 曲线上的游标跟着"帧"走而不是跟着播放头走：60fps 重设 option 会把主线程吃掉
watch(idx, (i) => markLine(points.value[i]?.offsetSec ?? 0))

function playLine(sec: number) {
  return {
    symbol: 'none', silent: true, label: { show: false },
    lineStyle: { color: '#C0392B', width: 1 },
    data: [{ xAxis: sec }],
  }
}

function markLine(sec: number) {
  const thr = meta.value.batteryThreshold ?? 20
  fChart?.setOption({ series: [{ id: 'alt', markLine: playLine(sec) }] })
  // 电量图的阈值线是常驻的，重设游标时必须一起回写，否则 markLine 是整体替换、阈值线会被抹掉
  bChart?.setOption({
    series: [{
      id: 'bat',
      markLine: {
        symbol: 'none', silent: true,
        label: { formatter: '返航阈值', fontSize: 10, position: 'insideEndTop' },
        lineStyle: { color: '#D39A2B', type: 'dashed' },
        data: [{ yAxis: thr, label: { formatter: '返航阈值', fontSize: 10 } },
               { xAxis: sec, lineStyle: { color: '#C0392B', width: 1 }, label: { show: false } }],
      },
    }],
  })
}

function buildCharts() {
  if (!flightEl.value || !battEl.value) return
  fChart = fChart || echarts.init(flightEl.value)
  bChart = bChart || echarts.init(battEl.value)
  const xs = points.value.map(p => p.offsetSec)
  const vio = points.value.map((p, i) => (p.violation ? { xAxis: p.offsetSec, yAxis: p.altitude } : null))
    .filter(Boolean)
  fChart.setOption({
    title: { text: '高度 / 速度', left: 'center', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis', formatter: (ps: any[]) => frameTip(ps) },
    grid: { left: 46, right: 46, top: 42, bottom: 26 },
    xAxis: { type: 'value', name: '秒', max: duration.value || 1, axisLabel: { formatter: (v: number) => fmtDur(v) } },
    yAxis: [
      { type: 'value', name: '高度m', scale: true },
      { type: 'value', name: '速度m/s', scale: true },
    ],
    series: [
      { id: 'alt', name: '高度', type: 'line', data: points.value.map((p, i) => [xs[i], p.altitude]),
        showSymbol: false, lineStyle: { color: '#23406E' }, areaStyle: { opacity: 0.08 },
        markPoint: { symbolSize: 34, itemStyle: { color: '#C0392B' }, data: vio as any,
          label: { formatter: '违规', fontSize: 9, color: '#fff' } } },
      { id: 'spd', name: '速度', type: 'line', yAxisIndex: 1, data: points.value.map((p, i) => [xs[i], p.speed]),
        showSymbol: false, lineStyle: { color: '#C8A45C', type: 'dashed' } },
    ],
  }, true)
  bChart.setOption({
    title: { text: `电量（返航阈值 ${meta.value.batteryThreshold ?? 20}%）`, left: 'center', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis', formatter: (ps: any[]) => frameTip(ps) },
    grid: { left: 42, right: 18, top: 42, bottom: 26 },
    xAxis: { type: 'value', name: '秒', max: duration.value || 1, axisLabel: { formatter: (v: number) => fmtDur(v) } },
    yAxis: { type: 'value', name: '%', max: 100 },
    series: [{
      id: 'bat', name: '电量', type: 'line', data: points.value.map((p, i) => [xs[i], p.battery]),
      showSymbol: false, lineStyle: { color: '#2E7D52' },
    }],
  }, true)
  markLine(0)
  for (const c of [fChart, bChart]) {
    c.off('click')
    c.on('click', (e: any) => {
      const p = Array.isArray(e.data) ? points.value.find(x => x.offsetSec === e.data[0]) : null
      if (p) { seek(p.offsetSec); playing.value = false }
    })
  }
}

function frameTip(ps: any[]) {
  const p = points.value.find(x => x.offsetSec === ps[0]?.value?.[0])
  if (!p) return ''
  return `${p.t}<br/>高度 ${p.altitude.toFixed(1)} m · 速度 ${p.speed.toFixed(1)} m/s<br/>`
    + `电量 ${p.battery}% · 航向 ${p.direction}°`
    + (p.violation ? `<br/><b style="color:#C0392B">违规：${p.violationName || p.violationType}</b>` : '')
}

async function initMap() {
  if (map) return true
  try {
    const AMap = await loadAmap()
    map = new AMap.Map(mapEl.value, { zoom: 12, center: [120.85, 27.96], viewMode: '2D' })
    map.addControl && map.addControl(new AMap.Scale())
    return true
  } catch {
    mapFail.value = true
    return false
  }
}

function rebuildOverlays() {
  if (!map || !points.value.length) return
  const AMap = (window as any).AMap
  map.remove(map.getAllOverlays())
  vioMarkers = []
  const path = points.value.map(p => [p.lng, p.lat] as [number, number])
  lineDone = new AMap.Polyline({ path, strokeColor: '#C8A45C', strokeWeight: 4, showDir: true, opacity: 0.95 })
  // 空 path 会让 AMap 在控制台报 "error Polyline path"；给整条航线做初值，
  // 紧接着的 drawProgress() 会按播放头改成真正的"剩余航段"。
  lineRest = new AMap.Polyline({ path, strokeColor: '#9aa4b8', strokeWeight: 3, strokeStyle: 'dashed', opacity: 0.6 })
  map.add([lineDone, lineRest])
  new AMap.Marker({ position: path[0], map, title: '起飞' })
  new AMap.Marker({ position: path[path.length - 1], map, title: '降落' })
  for (const p of vioPoints.value) {
    const m = new AMap.CircleMarker({
      center: [p.lng, p.lat], radius: 7, strokeColor: '#fff', strokeWeight: 2,
      fillColor: '#C0392B', fillOpacity: 0.95, cursor: 'pointer',
      title: `${p.t} ${p.violationName || p.violationType || '违规'}`,
    })
    m.on('click', () => { seek(p.offsetSec); playing.value = false })
    map.add(m)
    vioMarkers.push(m)
  }
  plane = new AMap.Marker({
    position: path[0], map, zIndex: 200,
    content: '<div style="width:16px;height:16px;border-radius:50%;background:#23406E;border:3px solid #fff;box-shadow:0 0 8px rgba(35,64,110,.8)"></div>',
    offset: new AMap.Pixel(-8, -8),
  })
  map.setFitView()
  drawProgress()
}

function drawProgress() {
  if (!map || !lineDone || !points.value.length) return
  const i = idx.value
  const pos = markerPos()
  lineDone.setPath([...points.value.slice(0, i + 1).map(p => [p.lng, p.lat]), pos])
  lineRest.setPath([pos, ...points.value.slice(i + 1).map(p => [p.lng, p.lat])])
  plane?.setPosition(pos)
}

async function pick(t: TrackItem) {
  cur.value = t.planId
  loading.value = true
  playing.value = false
  try {
    const d: any = await request(`/monitor/tracks/${t.planId}`)
    points.value = d.points || []
    meta.value = d
    playhead.value = 0
    // 自适应倍速：让整条轨迹在 1 分钟内播完，档位取不小于该值的最小档
    const need = points.value.length ? points.value[points.value.length - 1].offsetSec / 60 : 1
    speed.value = SPEEDS.find(s => s >= need) || SPEEDS[SPEEDS.length - 1]
    await nextTick()
    if (await initMap()) rebuildOverlays()
    buildCharts()
  } finally {
    loading.value = false
  }
}

function exportCsv() {
  const head = '序号,时间,经度,纬度,高度m,速度m每秒,电量pct,航向deg,是否违规,违规类型'
  const rows = points.value.map((p, i) => [
    i + 1, p.ts, p.lng, p.lat, p.altitude, p.speed, p.battery, p.direction,
    p.violation ? 1 : 0, p.violationType || '',
  ].join(','))
  const blob = new Blob(['\ufeff' + [head, ...rows].join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `轨迹-${meta.value.planNo || cur.value}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
  ElMessage.success(`已导出 ${rows.length} 帧轨迹`)
}

onMounted(async () => {
  const d: any = await request('/monitor/tracks')
  list.value = d.list || []
  await nextTick()
  if (await initMap() && list.value.length) pick(list.value[0])
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  fChart?.dispose(); bChart?.dispose()
  map?.destroy()
  map = null; fChart = null; bChart = null
})
</script>

<style scoped>
.row { padding: 10px; border-radius: 4px; cursor: pointer; margin-bottom: 6px; }
.row:hover { background: #f2f5fb; }
.row.on { background: #e6ecf7; border-left: 3px solid var(--ts-primary); }
.row-top { display: flex; align-items: center; gap: 6px; }
.head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; font-size: 13px; }
.map { height: 46vh; border: 1px solid #e2e5ee; border-radius: 4px; position: relative; }
.map-fail { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #909399; }
.controls { display: flex; align-items: center; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.spacer { flex: 1; }
.time { font-size: 12px; color: #606266; font-variant-numeric: tabular-nums; white-space: nowrap; }
.time em { color: #909399; font-style: normal; }
.timeline { position: relative; margin-top: 4px; }
.vio-tick { position: absolute; top: -6px; width: 3px; height: 14px; background: #C0392B;
  transform: translateX(-1.5px); cursor: pointer; }
.readout { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px; font-size: 13px; color: #606266; }
.readout b { color: var(--ts-primary-dark); font-variant-numeric: tabular-nums; }
.readout .bad b { color: #C0392B; }
.chart { height: 220px; margin-top: 12px; }
</style>
