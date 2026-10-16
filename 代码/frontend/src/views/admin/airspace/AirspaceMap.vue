<template>
  <div class="wrap">
    <!-- 左：规则查询 + 图层开关（需求 4.2.4-1 空域数据可视化与规则查询） -->
    <div class="ts-card side">
      <div class="side-head">
        <b class="card-title">空域一张图</b>
        <el-button v-if="can('airspace:draw')" size="small" :type="drawing ? 'warning' : 'primary'"
          @click="toggleDraw">{{ drawing ? `完成（${verts.length} 点）` : '绘制新空域' }}</el-button>
      </div>
      <p v-if="drawing" class="tip draw-tip">在地图上依次点击落点，凑满 3 点后按「完成」闭合并填写属性；右键撤销上一个点。</p>

      <el-input v-model="keyword" size="small" placeholder="规则查询：名称 / 编号 / 区域" clearable />
      <div class="layers">
        <div class="layers-title">图层</div>
        <el-checkbox v-for="t in TYPES" :key="t" v-model="layerOn[t]" size="small">
          <i class="dot" :style="{ background: TYPE_COLOR[t] }" />{{ TYPE_NAME[t] }}
          <span class="cnt">{{ countOf(t) }}</span>
        </el-checkbox>
        <el-checkbox v-model="fenceOn" size="small">
          <i class="dot" :style="{ background: FENCE_COLOR }" />电子围栏<span class="cnt">{{ fences.length }}</span>
        </el-checkbox>
        <el-checkbox v-model="liveOn" size="small">
          <i class="dot live" />在飞目标<span class="cnt">{{ flying.length }}</span>
        </el-checkbox>
        <el-checkbox v-model="draftOn" size="small">显示未生效空域（待审核 / 已驳回）</el-checkbox>
      </div>

      <div class="list" v-loading="loading">
        <div v-for="a in visible" :key="a.id" class="row" :class="{ cur: a.id === curId }" @click="focus(a)">
          <div class="row-top">
            <span class="nm">{{ a.airspaceName }}</span>
            <el-tag size="small" :type="TAG_TYPE[a.airspaceType]" effect="plain">{{ a.typeName }}</el-tag>
          </div>
          <div class="row-sub">
            <span class="num">{{ a.airspaceNo }}</span> · {{ a.sourceName }}
            <span v-if="a.altitudeMax"> · 限高 {{ a.altitudeMax }}m</span>
          </div>
          <div class="row-sub">
            <el-tag size="small" :type="a.auditStatus === 1 ? 'success' : a.auditStatus === 0 ? 'warning' : 'danger'"
              effect="plain">{{ a.auditName }}</el-tag>
            <el-tag v-if="a.status === 2" size="small" type="info" effect="plain">已失效</el-tag>
            <span v-if="a.controlEndAt" class="ctl">管制至 {{ a.controlEndAt }}</span>
          </div>
        </div>
        <el-empty v-if="!loading && !visible.length" description="没有匹配的空域" :image-size="60" />
      </div>

      <div v-if="cur" class="detail">
        <div class="detail-head">
          <b>{{ cur.airspaceName }}</b>
          <el-button text size="small" @click="curId = 0">×</el-button>
        </div>
        <p>{{ cur.controlReason || '（无管制说明）' }}</p>
        <p class="num">高度 {{ fmtAltitudeBand(cur.altitudeMin, cur.altitudeMax, cur.airspaceType) }} · {{ cur.regionName || '未标注区域' }}</p>
        <p v-if="cur.controlStartAt || cur.controlEndAt" class="num">
          管制时段 {{ cur.controlStartAt || '—' }} ~ {{ cur.controlEndAt || '—' }}
        </p>
        <p>审核：{{ cur.auditName }}
          <span v-if="cur.auditedBy" class="muted">（{{ cur.auditedBy }} · {{ cur.auditedAt }}）</span>
        </p>
        <p v-if="cur.auditOpinion" class="muted">{{ cur.auditOpinion }}</p>
        <div class="detail-acts">
          <template v-if="can('airspace:audit') && cur.auditStatus === 0">
            <el-button size="small" type="success" @click="doAudit(1)">通过并生效</el-button>
            <el-button size="small" type="danger" @click="doAudit(2)">驳回</el-button>
          </template>
          <el-button v-if="can('airspace:draw')" size="small" @click="toggleStatus">
            {{ cur.status === 1 ? '置为失效' : '恢复生效' }}
          </el-button>
        </div>
      </div>
    </div>

    <!-- 右：地图 + 动态监控（需求 4.2.4-4 实时轨迹对比、告警弹窗与声音） -->
    <div class="ts-card main">
      <div class="bar">
        <span class="legend">
          <i class="lg" style="background: rgba(192, 57, 43, .3)" />禁飞
          <i class="lg" style="background: rgba(211, 154, 43, .3)" />限飞
          <i class="lg" style="background: rgba(46, 125, 82, .3)" />可飞
          <i class="lg" style="background: rgba(142, 68, 173, .3)" />管制
          <i class="lg" style="background: rgba(31, 111, 178, .35)" />围栏
        </span>
        <div class="spacer" />
        <el-checkbox v-if="can('monitor:realtime')" v-model="popupOn" size="small">告警弹窗</el-checkbox>
        <el-checkbox v-if="can('monitor:realtime')" v-model="soundOn" size="small">声音</el-checkbox>
        <el-button size="small" :loading="syncing" @click="doSync">空域数据同步</el-button>
      </div>

      <div v-if="mapError" class="fallback">
        <el-alert :title="mapError" type="warning" :closable="false" show-icon />
        <p class="muted">地图不可用时，左侧台账的规则查询、审核与同步仍可正常使用；绘制空域需要地图。</p>
      </div>
      <div v-show="!mapError" ref="mapEl" class="map" />

      <div v-if="can('monitor:track')" class="live-bar">
        <span class="lb-title">实时轨迹对比</span>
        <el-select v-model="compareId" size="small" placeholder="选择在飞任务" clearable style="width: 300px">
          <el-option v-for="p in flying" :key="p.id" :value="p.id"
            :label="`${p.planNo} ${p.aircraftNo || ''} · ${p.pilotName || '未指派'}（${p.taskName || '—'}）`" />
        </el-select>
        <span v-if="trackStat" class="track-stat">
          实际帧 {{ trackStat.frames }} · 违规帧 <b :class="{ bad: trackStat.violation > 0 }">{{ trackStat.violation }}</b>
          · 末点高度 {{ trackStat.altitude }}m
        </span>
        <span v-else class="muted">计划航线（蓝虚线）与飞出的实际轨迹（红实线）叠在同一张图上比对</span>
      </div>
    </div>

    <!-- 绘制落库：后端置为「待审核」，审核通过才参与合规判定 -->
    <el-dialog v-model="dlg" title="新绘空域属性" width="520px">
      <el-form :model="form" label-width="92px" size="small">
        <el-form-item label="名称" required>
          <el-input v-model="form.airspaceName" placeholder="如：瓯江口临时管制区" />
        </el-form-item>
        <el-form-item label="类型" required>
          <el-radio-group v-model="form.airspaceType">
            <el-radio v-for="t in TYPES" :key="t" :value="t">{{ TYPE_NAME[t] }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="限低(m)">
          <el-input-number v-model.number="form.altitudeMin" :min="0" :max="3000" controls-position="right" />
        </el-form-item>
        <el-form-item label="限高(m)">
          <el-input-number v-model.number="form.altitudeMax" :min="0" :max="3000" controls-position="right" />
          <span class="muted" style="margin-left:8px">禁飞区可填 0</span>
        </el-form-item>
        <el-form-item label="所在区域">
          <el-input v-model="form.regionName" placeholder="如：龙湾 / 洞头" />
        </el-form-item>
        <template v-if="form.airspaceType === 4">
          <el-form-item label="管制时段" required>
            <el-date-picker v-model="controlRange" type="datetimerange" value-format="YYYY-MM-DD HH:mm:ss"
              style="width: 100%" />
          </el-form-item>
        </template>
        <el-form-item label="说明 / 原因">
          <el-input v-model="form.controlReason" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="顶点">
          <span class="num">{{ verts.length }} 个（点击地图可继续调整前先取消重绘）</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="cancelDraw">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="save">提交审核</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import request from '@/utils/request'
import { useUserStore } from '@/stores/user'
import { loadAmap, amapError } from '@/composables/useAmap'
import {
  auditAirspaceApi, createAirspaceApi, listAirspacesAdminApi, listGeofencesApi,
  syncAirspacesApi, updateAirspaceApi,
  type AirspaceAdminVO, type GeofenceVO,
} from '@/api/airspace'
import { listPlansApi, type FlightPlanVO } from '@/api/dispatch'
import { fmtAltitudeBand } from '@/constants/dict'

const TYPES = [1, 2, 3, 4] as const
const TYPE_NAME: Record<number, string> = { 1: '禁飞区', 2: '限飞区', 3: '可飞区', 4: '临时管制' }
const TYPE_COLOR: Record<number, string> = { 1: '#C0392B', 2: '#D39A2B', 3: '#2E7D52', 4: '#8E44AD' }
const TAG_TYPE: Record<number, 'info' | 'warning' | 'primary' | 'success' | 'danger'> =
  { 1: 'danger', 2: 'warning', 3: 'success', 4: 'primary' }
const FENCE_COLOR = '#1F6FB2'
/** 违规类型名与后端 VIO_TYPE_NAME 同口径，轨迹帧里带的是数字 */
const VIO_NAME: Record<number, string> = { 1: '闯入禁飞区', 2: '航线偏航', 3: '超时飞行', 4: '超高飞行', 5: '进入电子围栏' }

const userStore = useUserStore()
const can = (c: string) => userStore.hasPerm(c)

const mapEl = ref<HTMLElement | null>(null)
const mapError = ref('')
const loading = ref(true)
const saving = ref(false)
const syncing = ref(false)

const list = ref<AirspaceAdminVO[]>([])
const fences = ref<GeofenceVO[]>([])
const flying = ref<FlightPlanVO[]>([])

const keyword = ref('')
const layerOn = reactive<Record<number, boolean>>({ 1: true, 2: true, 3: true, 4: true })
const fenceOn = ref(true)
const liveOn = ref(true)
const draftOn = ref(true)
const popupOn = ref(can('monitor:realtime'))
const soundOn = ref(true)

const curId = ref(0)
const cur = computed(() => list.value.find((a) => a.id === curId.value) || null)

const compareId = ref<number | null>(null)
const trackStat = ref<{ frames: number; violation: number; altitude: number } | null>(null)

const visible = computed(() => {
  const k = keyword.value.trim()
  return list.value.filter((a) => {
    if (!layerOn[a.airspaceType]) return false
    if (!draftOn.value && a.auditStatus !== 1) return false
    if (!k) return true
    return [a.airspaceName, a.airspaceNo, a.regionName || ''].some((s) => s.includes(k))
  })
})
function countOf(t: number) {
  return list.value.filter((a) => a.airspaceType === t).length
}

// ---------------- 地图 ----------------
let AMap: any = null
let map: any = null
let staticOv: any[] = []
let draftLine: any = null
let trackOv: any[] = []
const liveMk: Record<number, any> = {}
const polyOf: Record<number, any> = {}

async function initMap() {
  if (!mapEl.value) return
  try {
    AMap = await loadAmap()
  } catch {
    mapError.value = amapError.value || '地图加载失败'
    return
  }
  map = new AMap.Map(mapEl.value, { zoom: 12, center: [120.93, 27.95], viewMode: '2D' })
  map.addControl && map.addControl(new AMap.Scale())
  map.on('click', (e: any) => {
    if (!drawing.value) return
    addVertex(Number(e.lnglat.lng), Number(e.lnglat.lat))
  })
  // 右键 = 撤销上一个点：绘制中途手滑不必整块重来
  map.on('contextmenu', () => {
    if (drawing.value) removeLastVertex()
  })
  await renderStatic()
}

function clearStatic() {
  if (map && staticOv.length) map.remove(staticOv)
  staticOv = []
  for (const k of Object.keys(polyOf)) delete polyOf[Number(k)]
}

async function renderStatic() {
  if (!map || !AMap) return
  clearStatic()
  for (const a of visible.value) {
    const rings = a.geometry?.coordinates || []
    const color = TYPE_COLOR[a.airspaceType] || '#666'
    const inactive = a.auditStatus !== 1 || a.status !== 1
    for (const ring of rings) {
      const path = ring.map((c) => [c[0], c[1]] as [number, number])
      const pg = new AMap.Polygon({
        path,
        strokeColor: color, strokeWeight: 2,
        strokeStyle: inactive ? 'dashed' : 'solid',
        strokeOpacity: inactive ? 0.6 : 0.9,
        fillColor: color, fillOpacity: inactive ? 0.05 : 0.14,
        bubble: true,
      })
      pg.on('click', () => { curId.value = a.id })
      map.add(pg)
      staticOv.push(pg)
      polyOf[a.id] = pg
      const txt = new AMap.Text({
        text: `${a.airspaceName}${inactive ? '（' + a.auditName + '）' : ''}`,
        position: path[0],
        style: {
          color, border: `1px solid ${color}`, 'border-radius': '3px',
          'background-color': 'rgba(255,255,255,.88)', 'font-size': '11px', padding: '1px 5px',
        },
      })
      map.add(txt)
      staticOv.push(txt)
    }
  }
  if (fenceOn.value) {
    for (const f of fences.value) {
      for (const ring of f.geometry?.coordinates || []) {
        const path = ring.map((c) => [c[0], c[1]] as [number, number])
        const pg = new AMap.Polygon({
          path, strokeColor: FENCE_COLOR, strokeWeight: 2, strokeStyle: 'dashed',
          fillColor: FENCE_COLOR, fillOpacity: f.status === 1 ? 0.12 : 0.04, bubble: true,
        })
        map.add(pg)
        staticOv.push(pg)
        const txt = new AMap.Text({
          text: `${f.fenceName}·${f.typeName}${f.altitudeLimit ? ' 限高' + f.altitudeLimit + 'm' : ''}`,
          position: path[0],
          style: {
            color: '#fff', 'border-radius': '3px', 'background-color': FENCE_COLOR,
            'font-size': '11px', padding: '1px 5px',
          },
        })
        map.add(txt)
        staticOv.push(txt)
      }
    }
  }
}

function focus(a: AirspaceAdminVO) {
  curId.value = a.id
  const pg = polyOf[a.id]
  if (pg && map) map.setFitView([pg], false, [60, 60, 60, 60])
}

// ---------------- 绘制 ----------------
const drawing = ref(false)
const dlg = ref(false)
const verts = ref<{ lng: number; lat: number }[]>([])
const controlRange = ref<[string, string] | null>(null)
const form = reactive<any>({
  airspaceName: '', airspaceType: 2, altitudeMin: 0, altitudeMax: 120,
  regionName: '', controlReason: '',
})

function toggleDraw() {
  if (drawing.value) { finishDraw(); return }
  if (!map) { ElMessage.warning(mapError.value || '地图未就绪，无法绘制'); return }
  drawing.value = true
  verts.value = []
  ElMessage.info('在地图上依次点击落点，右键撤销，够 3 点后点「完成」')
}

function addVertex(lng: number, lat: number) {
  verts.value.push({ lng, lat })
  redrawDraft()
}
function removeLastVertex() {
  verts.value.pop()
  redrawDraft()
}
function redrawDraft() {
  if (!map || !AMap) return
  if (draftLine) { map.remove(draftLine); draftLine = null }
  if (verts.value.length < 2) return
  draftLine = new AMap.Polygon({
    path: verts.value.map((v) => [v.lng, v.lat] as [number, number]),
    strokeColor: '#23406E', strokeWeight: 2, fillColor: '#23406E', fillOpacity: 0.18,
  })
  map.add(draftLine)
}

function finishDraw() {
  if (verts.value.length < 3) { ElMessage.warning('至少需要 3 个顶点'); return }
  drawing.value = false
  dlg.value = true
}
function cancelDraw() {
  dlg.value = false
  drawing.value = false
  verts.value = []
  if (map && draftLine) { map.remove(draftLine); draftLine = null }
}

async function save() {
  if (!form.airspaceName.trim()) { ElMessage.warning('请填写空域名称'); return }
  if (form.airspaceType === 4 && !controlRange.value) { ElMessage.warning('临时管制必须填管制时段'); return }
  const ring = [...verts.value.map((v) => [v.lng, v.lat]), [verts.value[0].lng, verts.value[0].lat]]
  saving.value = true
  try {
    const r = await createAirspaceApi({
      airspaceName: form.airspaceName,
      airspaceType: form.airspaceType,
      altitudeMin: form.altitudeMin, altitudeMax: form.altitudeMax,
      regionName: form.regionName, controlReason: form.controlReason,
      controlStartAt: controlRange.value?.[0] || null,
      controlEndAt: controlRange.value?.[1] || null,
      geometry: { type: 'Polygon', coordinates: [ring] },
    })
    ElMessage.success(`已提交审核（${r.airspaceNo}），通过后才会参与合规判定`)
    cancelDraw()
    await load()
  } finally {
    saving.value = false
  }
}

// ---------------- 台账动作 ----------------
async function doAudit(result: 1 | 2) {
  if (!cur.value) return
  let opinion = ''
  if (result === 2) {
    const r = await ElMessageBox.prompt('驳回原因（会随站内信回送给绘制人）', `驳回 ${cur.value.airspaceName}`, {
      inputPlaceholder: '如：边界覆盖在用航线，请缩小范围后重新提交',
    }).catch(() => null)
    if (!r) return
    opinion = String(r.value || '')
  }
  await auditAirspaceApi(cur.value.id, result, opinion)
  ElMessage.success(result === 1 ? '已通过，空域纳入合规判定' : '已驳回，空域置为失效')
  await load()
}

async function toggleStatus() {
  if (!cur.value) return
  const next = cur.value.status === 1 ? 2 : 1
  await updateAirspaceApi(cur.value.id, { status: next })
  ElMessage.success(next === 1 ? '已恢复生效' : '已置为失效')
  await load()
}

async function doSync() {
  syncing.value = true
  try {
    const r = await syncAirspacesApi()
    ElMessage.success(`同步完成 ${r.syncedAt}：${r.expired.length ? r.expired.join('、') + ' 已过管制时段置失效' : '无过期临时管制'}`)
    await load()
  } finally {
    syncing.value = false
  }
}

// ---------------- 在飞目标 + 轨迹对比 + 告警 ----------------
let liveTimer: number | null = null
let alertTimer: number | null = null
const seenAlerts = new Set<string>()
let alertPrimed = false
let audioCtx: AudioContext | null = null

function beep() {
  if (!soundOn.value) return
  try {
    audioCtx = audioCtx || new AudioContext()
    const o = audioCtx.createOscillator()
    const g = audioCtx.createGain()
    o.type = 'square'
    o.frequency.value = 820
    g.gain.value = 0.05
    o.connect(g); g.connect(audioCtx.destination)
    o.start()
    o.stop(audioCtx.currentTime + 0.3)
  } catch { /* 未授权自动播放的浏览器直接跳过声音 */ }
}

async function loadLive() {
  try {
    const r = await listPlansApi({ page: 1, pageSize: 50, status: 3 })
    flying.value = r.list
    renderLive()
    if (compareId.value && !r.list.some((p) => p.id === compareId.value)) {
      // 任务落地后帧表就定格了，这里保留最后一次统计，只把标记清掉
      clearLive()
    }
  } catch { /* 在飞列表不阻塞地图 */ }
}

function clearLive() {
  if (map) for (const id of Object.keys(liveMk)) map.remove(liveMk[Number(id)])
  for (const k of Object.keys(liveMk)) delete liveMk[Number(k)]
}

function renderLive() {
  if (!map || !AMap) return
  const ids = new Set<number>()
  for (const p of flying.value) {
    if (!liveOn.value || p.currentLng == null || p.currentLat == null) continue
    ids.add(p.id)
    const pos = [Number(p.currentLng), Number(p.currentLat)]
    if (liveMk[p.id]) { liveMk[p.id].setPosition(pos); continue }
    const mk = new AMap.Marker({
      position: pos,
      content: `<div style="background:#C0392B;color:#fff;border-radius:50%;width:28px;height:28px;display:flex;
        align-items:center;justify-content:center;font-size:12px;border:2px solid #fff;
        box-shadow:0 0 0 rgba(192,57,43,.6);animation:ts-pulse 1.6s infinite">机</div>`,
      title: `${p.aircraftNo || p.planNo} ${p.pilotName || ''}`,
    })
    map.add(mk)
    liveMk[p.id] = mk
  }
  for (const id of Object.keys(liveMk).map(Number)) {
    if (!ids.has(id)) { map.remove(liveMk[id]); delete liveMk[id] }
  }
}

interface TrackPoint { lng: number; lat: number; altitude: number; speed: number; violation: number; violationType: number | null; t: string }

async function loadTrack() {
  trackStat.value = null
  if (!map || !AMap) return
  if (trackOv.length) { map.remove(trackOv); trackOv = [] }
  if (!compareId.value) return
  const plan = flying.value.find((p) => p.id === compareId.value)
  const r = await request<{ points: TrackPoint[] }>(`/monitor/tracks/${compareId.value}`)
  const pts = r.points || []
  if (plan?.route?.length) {
    const line = new AMap.Polyline({
      path: plan.route.map((x) => [x.lng, x.lat] as [number, number]),
      strokeColor: '#3D6B9E', strokeWeight: 4, strokeStyle: 'dashed', strokeOpacity: 0.95,
    })
    map.add(line); trackOv.push(line)
  }
  if (pts.length > 1) {
    const act = new AMap.Polyline({
      path: pts.map((x) => [x.lng, x.lat] as [number, number]),
      strokeColor: '#C0392B', strokeWeight: 4, strokeOpacity: 0.9, showDir: true,
    })
    map.add(act); trackOv.push(act)
  }
  let vio = 0
  for (const x of pts) {
    if (!x.violation) continue
    vio++
    const dot = new AMap.CircleMarker({
      center: [x.lng, x.lat], radius: 7, strokeColor: '#C0392B', strokeWeight: 2,
      fillColor: '#F5C2C2', fillOpacity: 0.9,
    })
    dot.on('click', () => ElMessage.warning(
      `${x.t} ${(VIO_NAME[x.violationType || 0] || '违规')}：高度 ${x.altitude}m，速度 ${x.speed}m/s`))
    map.add(dot); trackOv.push(dot)
  }
  trackStat.value = {
    frames: pts.length, violation: vio,
    altitude: pts.length ? Math.round(pts[pts.length - 1].altitude) : 0,
  }
  if (map.setFitView && trackOv.length) map.setFitView(trackOv, false, [60, 60, 60, 60])
}

async function loadAlerts() {
  if (!popupOn.value) return
  let r
  try {
    r = await request<{ list: any[] }>('/monitor/alerts')
  } catch { return }
  for (const x of r.list || []) {
    const key = `${x.source}-${x.refId ?? x.target}-${x.type}-${x.time || ''}`
    if (seenAlerts.has(key)) continue
    seenAlerts.add(key)
    // 第一次进页面只登记历史告警，不然一进来就被昨天的记录刷屏
    if (!alertPrimed) continue
    if (x.level !== '严重' && x.level !== '警告') continue
    ElNotification({
      title: `${x.level} · ${x.type}`,
      message: `${x.target || ''} ${x.detail || ''}`.trim(),
      type: x.level === '严重' ? 'error' : 'warning',
      duration: 6000,
    })
    if (x.level === '严重') beep()
  }
  alertPrimed = true
}

async function load() {
  loading.value = true
  try {
    const [as, gs] = await Promise.all([
      listAirspacesAdminApi({ all: 1 }),
      listGeofencesApi().catch(() => ({ list: [] as GeofenceVO[], total: 0 })),
    ])
    list.value = as.list
    fences.value = gs.list
  } finally {
    loading.value = false
  }
  await renderStatic()
}

watch(visible, () => { renderStatic() })
watch(fenceOn, () => { renderStatic() })
watch(liveOn, (v) => { if (!v) clearLive(); else renderLive() })
watch(compareId, () => { loadTrack() })

function startTimers() {
  stopTimers()
  liveTimer = window.setInterval(loadLive, 3000)
  if (can('monitor:realtime')) alertTimer = window.setInterval(loadAlerts, 6000)
}
function stopTimers() {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null }
  if (alertTimer) { clearInterval(alertTimer); alertTimer = null }
}

onMounted(async () => {
  await load()
  await nextTick()
  await initMap()
  await loadLive()
  loadAlerts()
  startTimers()
})
onActivated(() => {
  if (map) setTimeout(() => { try { map.resize() } catch { /* 地图未就绪 */ } }, 120)
  startTimers()
})
onDeactivated(() => { stopTimers() })
onBeforeUnmount(() => {
  stopTimers()
  if (map) map.destroy()
  map = null
})
</script>

<style lang="scss" scoped>
.wrap { display: grid; grid-template-columns: 300px 1fr; gap: 12px; height: calc(100vh - 56px - 32px); }
.side, .main { display: flex; flex-direction: column; overflow: hidden; padding: 12px; min-width: 0; }
.side-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.card-title { font-weight: 700; color: var(--ts-primary-dark); }
.tip { font-size: 12px; color: var(--ts-text-secondary); margin: 6px 0; }
.draw-tip { background: rgba(211, 154, 43, .1); border-left: 3px solid #D39A2B; padding: 6px 8px; }
.layers { margin: 10px 0 6px; border-top: 1px dashed var(--ts-border); padding-top: 8px; }
.layers-title { font-size: 12px; color: var(--ts-text-secondary); margin-bottom: 4px; }
.layers :deep(.el-checkbox) { display: flex; align-items: center; margin-right: 0; height: 24px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; flex: none; }
.dot.live { background: #C0392B; border-radius: 50%; }
.cnt { margin-left: 6px; font-size: 11px; color: var(--ts-text-secondary); }
.mutedish { color: var(--ts-text-secondary); font-style: normal; font-size: 11px; }
.list { flex: 1; overflow-y: auto; margin-top: 6px; }
.row { border: 1px solid var(--ts-border); border-radius: 5px; padding: 7px 8px; margin-bottom: 7px; cursor: pointer; }
.row:hover { border-color: var(--ts-gold); }
.row.cur { border-color: var(--ts-gold); background: rgba(200, 164, 92, .08); }
.row-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.nm { font-size: 13px; font-weight: 600; color: var(--ts-primary-dark); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-sub { font-size: 11.5px; color: var(--ts-text-secondary); margin-top: 3px; display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.ctl { margin-left: auto; }
.detail { border-top: 1px dashed var(--ts-border); margin-top: 8px; padding-top: 8px; font-size: 12.5px; }
.detail p { margin: 3px 0; color: var(--ts-text-regular); }
.detail-head { display: flex; align-items: center; justify-content: space-between; }
.detail-acts { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.bar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.spacer { flex: 1; }
.legend { font-size: 12px; color: var(--ts-text-secondary); display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.lg { display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin: 0 2px 0 8px; }
.map { flex: 1; border-radius: 4px; overflow: hidden; min-height: 320px; }
.fallback { margin-bottom: 8px; }
.live-bar { display: flex; align-items: center; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
.lb-title { font-size: 12.5px; font-weight: 600; color: var(--ts-primary-dark); }
.track-stat { font-size: 12.5px; color: var(--ts-text-regular); }
.track-stat .bad { color: #C0392B; }
</style>
