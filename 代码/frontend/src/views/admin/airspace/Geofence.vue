<template>
  <div class="wrap">
    <div class="ts-card side">
      <div class="side-head">
        <b class="card-title">电子围栏</b>
        <el-button v-if="can('airspace:draw')" size="small" :type="drawing ? 'warning' : 'primary'"
          @click="toggleDraw">{{ drawing ? `完成（${verts.length} 点）` : '绘制围栏' }}</el-button>
      </div>
      <p v-if="drawing" class="tip draw-tip">
        {{ editId ? '将重新设定这条围栏的边界。' : '地图依次点击落点，≥3 点后按「完成」闭合并填写参数；右键撤销上一点。' }}
      </p>

      <el-table :data="list" v-loading="loading" size="small" stripe>
        <el-table-column label="名称 / 类型" min-width="150">
          <template #default="{ row }">
            <div class="nm">{{ row.fenceName }}</div>
            <div class="sub">
              <el-tag size="small" :type="TAG_TYPE[row.fenceType]" effect="plain">{{ row.typeName }}</el-tag>
              <span class="muted">{{ row.merchantName }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="限制" width="120">
          <template #default="{ row }">
            <div class="sub num">{{ row.altitudeLimit ? `限高 ${row.altitudeLimit}m` : '不限高' }}</div>
            <div class="sub num">告警 {{ row.warnDistanceM ?? '—' }}m</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 1 ? 'success' : 'info'" effect="plain">{{ row.statusName }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="focus(row)">定位</el-button>
            <template v-if="can('airspace:draw')">
              <el-button link type="primary" size="small" @click="openEdit(row)">改参数</el-button>
              <el-button link :type="row.status === 1 ? 'warning' : 'success'" size="small" @click="toggleStatus(row)">
                {{ row.status === 1 ? '停用' : '启用' }}
              </el-button>
              <el-button link type="danger" size="small" @click="del(row)">删</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <el-alert class="rule" type="info" :closable="false" show-icon>
        <div>限入：进入即判违规；限出：起飞后飞出围栏判违规；接近告警：进入告警距离只提示、不记违规。</div>
        <div>限高只在围栏范围内生效，范围外的飞机不受这条围栏约束。</div>
        <div>围栏新增 / 启停会立刻给当时在飞的机长下发站内信预警，不需要等下一个航段。</div>
      </el-alert>
    </div>

    <div class="ts-card main">
      <div class="bar">
        <span class="legend">
          <i class="lg" :style="{ background: FENCE_COLOR }" />围栏
          <i class="lg" style="background: rgba(192, 57, 43, .3)" />禁飞
          <i class="lg" style="background: rgba(211, 154, 43, .3)" />限飞
          <i class="lg" style="background: rgba(46, 125, 82, .25)" />可飞
        </span>
        <div class="spacer" />
        <el-checkbox v-model="bgOn" size="small">显示空域底图</el-checkbox>
      </div>
      <div v-if="mapError" class="fallback">
        <el-alert :title="mapError" type="warning" :closable="false" show-icon />
        <p class="muted">地图不可用时仍可改参数、启停与删除；新绘围栏需要地图。</p>
      </div>
      <div v-show="!mapError" ref="mapEl" class="map" />
      <p class="muted">{{ mapHint }}</p>
    </div>

    <el-dialog v-model="dlg" :title="editId ? `编辑围栏：${form.fenceName}` : '新建电子围栏'" width="520px">
      <el-form :model="form" label-width="100px" size="small">
        <el-form-item label="围栏名称" required>
          <el-input v-model="form.fenceName" placeholder="如：洞头危化品码头限入围栏" />
        </el-form-item>
        <el-form-item label="围栏类型" required>
          <el-radio-group v-model="form.fenceType">
            <el-radio v-for="(n, t) in FENCE_TYPE" :key="t" :value="Number(t)">{{ n }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="适用商家">
          <el-select v-model="form.merchantId" clearable placeholder="留空 = 平台级（全部商家）" style="width: 100%">
            <el-option v-for="m in owners" :key="m.id" :value="m.id" :label="m.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="限高(m)">
          <el-input-number v-model.number="form.altitudeLimit" :min="0" :max="3000" controls-position="right" />
          <span class="muted" style="margin-left:8px">仅围栏范围内生效</span>
        </el-form-item>
        <el-form-item v-if="form.fenceType === 3" label="告警距离(m)" required>
          <el-input-number v-model.number="form.warnDistanceM" :min="50" :max="5000" :step="50" controls-position="right" />
        </el-form-item>
        <el-form-item label="顶点">
          <span class="num">{{ verts.length }} 个 · 保存后立即向在飞机长下发预警</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="closeDlg">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="save">保存并下发</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { loadAmap, amapError } from '@/composables/useAmap'
import {
  createGeofenceApi, deleteGeofenceApi, listAirspacesAdminApi, listFenceOwnersApi, listGeofencesApi,
  updateGeofenceApi,
  type AirspaceAdminVO, type GeofenceVO,
} from '@/api/airspace'

const FENCE_TYPE: Record<number, string> = { 1: '限入', 2: '限出', 3: '接近告警' }
const TAG_TYPE: Record<number, 'danger' | 'warning' | 'primary'> = { 1: 'danger', 2: 'warning', 3: 'primary' }
const FENCE_COLOR = '#1F6FB2'
const ASP_COLOR: Record<number, string> = { 1: '#C0392B', 2: '#D39A2B', 3: '#2E7D52', 4: '#8E44AD' }

const userStore = useUserStore()
const can = (c: string) => userStore.hasPerm(c)

const list = ref<GeofenceVO[]>([])
const airspaces = ref<AirspaceAdminVO[]>([])
const owners = ref<{ id: number; name: string }[]>([])
const loading = ref(true)
const saving = ref(false)
const bgOn = ref(true)

const mapEl = ref<HTMLElement | null>(null)
const mapError = ref('')
let AMap: any = null
let map: any = null
let fenceOv: any[] = []
let bgOv: any[] = []
let draft: any = null
const fencePoly: Record<number, any> = {}

const mapHint = computed(() => drawing.value
  ? `已取 ${verts.value.length} 个点，右键撤销，够 3 点后点「完成绘制」`
  : '点击围栏可高亮并打开对应记录')

async function initMap() {
  if (!mapEl.value) return
  try { AMap = await loadAmap() } catch {
    mapError.value = amapError.value || '地图加载失败'
    return
  }
  map = new AMap.Map(mapEl.value, { zoom: 12.5, center: [120.93, 27.95], viewMode: '2D' })
  map.addControl && map.addControl(new AMap.Scale())
  map.on('click', (e: any) => {
    if (!drawing.value) return
    verts.value.push({ lng: Number(e.lnglat.lng), lat: Number(e.lnglat.lat) })
    redrawDraft()
  })
  map.on('contextmenu', () => {
    if (drawing.value) { verts.value.pop(); redrawDraft() }
  })
  await render()
}

async function render() {
  if (!map || !AMap) return
  if (fenceOv.length) map.remove(fenceOv)
  if (bgOv.length) map.remove(bgOv)
  fenceOv = []
  bgOv = []
  for (const k of Object.keys(fencePoly)) delete fencePoly[Number(k)]

  if (bgOn.value) {
    for (const a of airspaces.value) {
      if (a.status !== 1 || a.auditStatus !== 1) continue
      const color = ASP_COLOR[a.airspaceType] || '#666'
      for (const ring of a.geometry?.coordinates || []) {
        const pg = new AMap.Polygon({
          path: ring.map((c) => [c[0], c[1]] as [number, number]),
          strokeColor: color, strokeWeight: 1, strokeOpacity: 0.55,
          strokeStyle: a.airspaceType === 3 ? 'dashed' : 'solid',
          fillColor: color, fillOpacity: 0.08, bubble: true,
        })
        map.add(pg); bgOv.push(pg)
      }
    }
  }
  for (const f of list.value) {
    for (const ring of f.geometry?.coordinates || []) {
      const pg = new AMap.Polygon({
        path: ring.map((c) => [c[0], c[1]] as [number, number]),
        strokeColor: FENCE_COLOR, strokeWeight: 3, strokeStyle: 'dashed',
        strokeOpacity: f.status === 1 ? 0.95 : 0.4,
        fillColor: FENCE_COLOR, fillOpacity: f.status === 1 ? 0.16 : 0.05, bubble: true,
      })
      pg.on('click', () => openEdit(f))
      map.add(pg); fenceOv.push(pg)
      fencePoly[f.id] = pg
      const txt = new AMap.Text({
        text: `${f.fenceName}｜${f.typeName}${f.status === 1 ? '' : '（停用）'}`,
        position: ring[0] as [number, number],
        style: {
          color: '#fff', 'background-color': f.status === 1 ? FENCE_COLOR : '#9aa3b2',
          'border-radius': '3px', 'font-size': '11px', padding: '1px 6px',
        },
      })
      map.add(txt); fenceOv.push(txt)
    }
  }
  if (list.value.length) map.setFitView(fenceOv, false, [50, 50, 50, 50])
}

function focus(f: GeofenceVO) {
  const pg = fencePoly[f.id]
  if (!pg || !map) { ElMessage.info('地图未就绪，无法定位'); return }
  map.setFitView([pg], false, [70, 70, 70, 70])
}

function redrawDraft() {
  if (!map || !AMap) return
  if (draft) { map.remove(draft); draft = null }
  if (verts.value.length < 2) return
  draft = new AMap.Polygon({
    path: verts.value.map((v) => [v.lng, v.lat] as [number, number]),
    strokeColor: FENCE_COLOR, strokeWeight: 3, fillColor: FENCE_COLOR, fillOpacity: 0.25,
  })
  map.add(draft)
}

// ---------------- 绘制 / 表单 ----------------
const drawing = ref(false)
const dlg = ref(false)
const editId = ref<number | null>(null)
const verts = ref<{ lng: number; lat: number }[]>([])
const form = reactive<any>({
  fenceName: '', fenceType: 1, merchantId: null as number | null,
  altitudeLimit: 60, warnDistanceM: 200,
})

function toggleDraw() {
  if (drawing.value) {
    if (verts.value.length < 3) { ElMessage.warning('至少需要 3 个顶点'); return }
    drawing.value = false
    editId.value = null
    dlg.value = true
    return
  }
  if (!map) { ElMessage.warning(mapError.value || '地图未就绪'); return }
  verts.value = []
  drawing.value = true
  ElMessage.info('地图点击落点绘出围栏边界，右键撤销，完成后点「完成」')
}

function closeDlg() {
  dlg.value = false
  drawing.value = false
  verts.value = []
  if (map && draft) { map.remove(draft); draft = null }
}

function ringFrom(pts: { lng: number; lat: number }[]) {
  const r = pts.map((p) => [p.lng, p.lat])
  r.push([pts[0].lng, pts[0].lat])
  return r
}

function openEdit(f: GeofenceVO) {
  editId.value = f.id
  // 改已有围栏只动参数：边界要改就在地图上重新绘制（避免误碰一下就把围栏挪走）
  verts.value = (f.geometry?.coordinates?.[0] || []).slice(0, -1).map((c) => ({ lng: c[0], lat: c[1] }))
  Object.assign(form, {
    fenceName: f.fenceName, fenceType: f.fenceType, merchantId: f.merchantId,
    altitudeLimit: f.altitudeLimit ?? 0, warnDistanceM: f.warnDistanceM ?? 200,
  })
  dlg.value = true
}

async function save() {
  if (!String(form.fenceName || '').trim()) { ElMessage.warning('请填写围栏名称'); return }
  if (form.fenceType === 3 && !form.warnDistanceM) { ElMessage.warning('接近告警围栏必须填告警距离'); return }
  if (!editId.value && verts.value.length < 3) { ElMessage.warning('请先在地图上绘出围栏边界'); return }
  const payload: Record<string, any> = {
    fenceName: form.fenceName, fenceType: form.fenceType,
    merchantId: form.merchantId ?? 0,
    altitudeLimit: form.altitudeLimit ?? null,
    warnDistanceM: form.fenceType === 3 ? form.warnDistanceM : (form.warnDistanceM || null),
  }
  if (verts.value.length >= 3) {
    payload.geometry = { type: 'Polygon', coordinates: [ringFrom(verts.value)] }
  }
  saving.value = true
  try {
    const r = editId.value
      ? await updateGeofenceApi(editId.value, payload)
      : await createGeofenceApi(payload)
    ElMessage.success(r.notified ? `已保存，${r.notified} 位在飞机长收到围栏预警` : '已保存，当前无在飞机长需下发')
    closeDlg()
    await load()
  } finally { saving.value = false }
}

async function toggleStatus(f: GeofenceVO) {
  const next = f.status === 1 ? 2 : 1
  const r = await updateGeofenceApi(f.id, { status: next })
  ElMessage.success(next === 1
    ? `已启用，${r.notified} 位在飞机长收到预警`
    : `已停用，合规判定不再计入这条围栏（${r.notified} 位在飞机长已收到通知）`)
  await load()
}

async function del(f: GeofenceVO) {
  const go = await ElMessageBox.confirm(
    `确认删除围栏「${f.fenceName}」？删除后不再对飞行做围栏判定。`, '删除围栏', { type: 'warning' })
    .catch(() => null)
  if (!go) return
  await deleteGeofenceApi(f.id)
  ElMessage.success('已删除')
  await load()
}

async function load() {
  loading.value = true
  try {
    list.value = (await listGeofencesApi()).list
  } finally { loading.value = false }
  await render()
}

watch(bgOn, render)

onMounted(async () => {
  await Promise.all([load(),
    listAirspacesAdminApi().then((r) => { airspaces.value = r.list }).catch(() => undefined),
    can('airspace:draw')
      ? listFenceOwnersApi().then((r) => { owners.value = r }).catch(() => undefined)
      : Promise.resolve(),
  ])
  await nextTick()
  await initMap()
})
onBeforeUnmount(() => { if (map) map.destroy(); map = null })
</script>

<style lang="scss" scoped>
.wrap { display: grid; grid-template-columns: 460px 1fr; gap: 12px; height: calc(100vh - 56px - 32px); }
.side, .main { display: flex; flex-direction: column; overflow: auto; padding: 12px; min-width: 0; }
.side-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.card-title { font-weight: 700; color: var(--ts-primary-dark); }
.tip { font-size: 12px; color: var(--ts-text-secondary); margin: 0 0 8px; }
.draw-tip { background: rgba(211, 154, 43, .1); border-left: 3px solid #D39A2B; padding: 6px 8px; }
.nm { font-size: 13px; color: var(--ts-primary-dark); font-weight: 600; }
.sub { font-size: 11.5px; color: var(--ts-text-secondary); display: flex; align-items: center; gap: 6px; margin-top: 2px; }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.rule { margin-top: 10px; line-height: 1.7; }
.bar { display: flex; align-items: center; margin-bottom: 8px; }
.spacer { flex: 1; }
.legend { font-size: 12px; color: var(--ts-text-secondary); display: flex; align-items: center; gap: 4px; }
.lg { display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin: 0 2px 0 8px; opacity: .7; }
.map { flex: 1; min-height: 360px; border-radius: 4px; overflow: hidden; }
.fallback { margin-bottom: 8px; }
</style>
