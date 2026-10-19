<template>
  <div class="page">
    <div class="ts-card head">
      <div class="stat">
        <div v-for="s in stats" :key="s.label" class="stat-item">
          <label>{{ s.label }}</label><b class="num">{{ s.value }}</b>
        </div>
      </div>
      <div class="acts">
        <el-input v-model="keyword" size="small" placeholder="名称 / 编号 / 区域" clearable style="width: 190px" />
        <el-select v-model="typeFilter" size="small" placeholder="全部类型" clearable style="width: 120px">
          <el-option v-for="(n, t) in TYPE_NAME" :key="t" :value="Number(t)" :label="n" />
        </el-select>
        <el-select v-model="auditFilter" size="small" placeholder="全部审核态" clearable style="width: 130px">
          <el-option :value="0" label="待审核" />
          <el-option :value="1" label="已通过" />
          <el-option :value="2" label="已驳回" />
        </el-select>
        <el-button size="small" :loading="syncing" @click="doSync">空管数据同步</el-button>
        <el-button v-if="can('airspace:draw')" size="small" type="primary" @click="openCreate">+ 新建空域</el-button>
      </div>
    </div>

    <el-alert v-if="syncMsg" class="syncmsg" :title="syncMsg" type="success" :closable="true" show-icon
      @close="syncMsg = ''" />

    <el-table :data="filtered" v-loading="loading" stripe size="small" class="ts-card table">
      <el-table-column prop="airspaceNo" label="空域编号" width="150" class-name="num" />
      <el-table-column prop="airspaceName" label="名称" min-width="150" />
      <el-table-column label="类型" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="TAG_TYPE[row.airspaceType]" effect="plain">{{ row.typeName }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="sourceName" label="数据来源" width="90" />
      <el-table-column label="高度区间" width="150">
        <template #default="{ row }">
          <span class="num">{{ fmtAltitudeBand(row.altitudeMin, row.altitudeMax, row.airspaceType) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="管制时段" width="180">
        <template #default="{ row }">
          <span v-if="row.controlStartAt" class="num">{{ row.controlStartAt }} ~ {{ row.controlEndAt }}</span>
          <span v-else class="muted">长期</span>
        </template>
      </el-table-column>
      <el-table-column label="审核" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.auditStatus === 1 ? 'success' : row.auditStatus === 0 ? 'warning' : 'danger'">
            {{ row.auditName }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 1 ? 'primary' : 'info'" effect="plain">{{ row.statusName }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="审核留痕" min-width="180">
        <template #default="{ row }">
          <span v-if="row.auditedBy" class="muted">{{ row.auditedBy }} · {{ row.auditedAt }}</span>
          <div v-if="row.auditOpinion" class="opinion">{{ row.auditOpinion }}</div>
          <span v-if="!row.auditedBy && !row.auditOpinion" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <template v-if="can('airspace:audit') && row.auditStatus === 0">
            <el-button link type="success" size="small" @click="doAudit(row, 1)">通过</el-button>
            <el-button link type="danger" size="small" @click="doAudit(row, 2)">驳回</el-button>
          </template>
          <el-button v-if="can('airspace:draw')" link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="can('airspace:draw')" link size="small" @click="toggleStatus(row)">
            {{ row.status === 1 ? '停用' : '启用' }}
          </el-button>
          <el-button v-if="can('airspace:audit')" link type="danger" size="small" @click="doDel(row)">下架</el-button>
        </template>
      </el-table-column>
    </el-table>
    <p class="muted note">
      自绘空域落库即为「待审核」，只有审核通过的空域才参与航线合规判定 —— 未审核的草稿不会把在飞的机器判成闯区。
      空管同步来源的空域由属地空管下发，平台侧只做起停与限高维护。
    </p>

    <!-- 新建 / 编辑：地图点选画多边形（4.2.4-2 空域绘制/编辑），矩形范围只作地图不可用时的兜底 -->
    <el-dialog v-model="dlg" :title="editId ? `编辑空域：${form.airspaceName}` : '新建空域'" width="900px"
      @opened="initDrawMap" @closed="destroyDrawMap">
      <div class="dlg-body">
        <div class="mapcol">
          <div ref="drawEl" class="draw-map" />
          <p v-if="mapError" class="muted">{{ mapError }} —— 可改用右侧「矩形范围」兜底绘制</p>
          <p v-else class="muted">
            地图依次点击落点画边界，右键撤销上一点；至少 3 个点。
            <el-button link type="primary" size="small" @click="clearVerts">清空重绘</el-button>
          </p>
          <div class="muted">当前边界：{{ geomHint }}</div>
        </div>
        <el-form :model="form" label-width="96px" size="small">
          <el-form-item label="名称" required>
            <el-input v-model="form.airspaceName" placeholder="如：洞头危化品码头禁飞区" />
          </el-form-item>
          <el-form-item label="类型" required>
            <el-radio-group v-model="form.airspaceType">
              <el-radio v-for="(n, t) in TYPE_NAME" :key="t" :value="Number(t)">{{ n }}</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="限低(m)">
            <el-input-number v-model.number="form.altitudeMin" :min="0" :max="3000" controls-position="right" />
          </el-form-item>
          <el-form-item label="限高(m)">
            <el-input-number v-model.number="form.altitudeMax" :min="0" :max="3000" controls-position="right" />
          </el-form-item>
          <el-form-item label="所在区域">
            <el-input v-model="form.regionName" placeholder="如：洞头区" />
          </el-form-item>
          <el-form-item label="矩形范围">
            <div class="bounds">
              <span>西 <el-input-number v-model.number="form.west" :step="0.005" :precision="4" size="small" controls-position="right" /></span>
              <span>东 <el-input-number v-model.number="form.east" :step="0.005" :precision="4" size="small" controls-position="right" /></span>
              <span>南 <el-input-number v-model.number="form.south" :step="0.005" :precision="4" size="small" controls-position="right" /></span>
              <span>北 <el-input-number v-model.number="form.north" :step="0.005" :precision="4" size="small" controls-position="right" /></span>
            </div>
            <div class="muted">{{ drawnEnough ? '已按地图顶点保存，这一栏只在没画顶点时生效' : boundsHint }}（改边界会重新进入待审核）</div>
          </el-form-item>
          <template v-if="form.airspaceType === 4">
            <el-form-item label="管制时段" required>
              <el-date-picker v-model="controlRange" type="datetimerange" value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%" />
            </el-form-item>
          </template>
          <el-form-item label="说明 / 原因">
            <el-input v-model="form.controlReason" type="textarea" :rows="2" />
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button size="small" @click="dlg = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="save">
          {{ editId ? '保存' : '提交审核' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores/user'
import {
  auditAirspaceApi, createAirspaceApi, deleteAirspaceApi, listAirspacesAdminApi,
  syncAirspacesApi, updateAirspaceApi,
  type AirspaceAdminVO, type AirspacePayload,
} from '@/api/airspace'
import { fmtAltitudeBand } from '@/constants/dict'
import { loadAmap, amapError } from '@/composables/useAmap'

const TYPE_NAME: Record<number, string> = { 1: '禁飞区', 2: '限飞区', 3: '可飞区', 4: '临时管制' }
const TAG_TYPE: Record<number, 'danger' | 'warning' | 'success' | 'primary'> =
  { 1: 'danger', 2: 'warning', 3: 'success', 4: 'primary' }

const userStore = useUserStore()
const can = (c: string) => userStore.hasPerm(c)

const list = ref<AirspaceAdminVO[]>([])
const loading = ref(true)
const syncing = ref(false)
const saving = ref(false)
const syncMsg = ref('')

const keyword = ref('')
const typeFilter = ref<number | ''>('')
const auditFilter = ref<number | ''>('')

const filtered = computed(() => list.value.filter((a) => {
  if (typeFilter.value !== '' && a.airspaceType !== typeFilter.value) return false
  if (auditFilter.value !== '' && a.auditStatus !== auditFilter.value) return false
  const k = keyword.value.trim()
  if (!k) return true
  return [a.airspaceName, a.airspaceNo, a.regionName || '', a.controlReason || ''].some((s) => s.includes(k))
}))

const stats = computed(() => [
  { label: '台账总数', value: list.value.length },
  { label: '生效中', value: list.value.filter((a) => a.status === 1 && a.auditStatus === 1).length },
  { label: '待审核', value: list.value.filter((a) => a.auditStatus === 0).length },
  { label: '已驳回', value: list.value.filter((a) => a.auditStatus === 2).length },
  { label: '失效', value: list.value.filter((a) => a.status === 2).length },
])

async function load() {
  loading.value = true
  try { list.value = (await listAirspacesAdminApi({ all: 1 })).list } finally { loading.value = false }
}

async function doSync() {
  syncing.value = true
  try {
    const r = await syncAirspacesApi()
    syncMsg.value = `${r.syncedAt} 同步完成 · 过期临时管制置失效 ${r.expired.length} 个`
      + (r.expired.length ? `：${r.expired.join('、')}` : '')
      + `；${r.stat.map((s) => `${s.source}${s.status} ${s.count}`).join(' / ')}`
    await load()
  } finally { syncing.value = false }
}

async function doAudit(row: AirspaceAdminVO, result: 1 | 2) {
  let opinion = ''
  if (result === 2) {
    const r = await ElMessageBox.prompt(`驳回「${row.airspaceName}」，请写明原因`, '空域驳回', {
      inputPlaceholder: '如：与在用航线冲突，请缩小北侧边界',
    }).catch(() => null)
    if (!r) return
    opinion = String(r.value || '')
  } else {
    const go = await ElMessageBox.confirm(
      `通过后「${row.airspaceName}」立即纳入航线合规判定与飞行计划自动校验，确认？`,
      '空域审核', { type: 'warning' }).catch(() => null)
    if (!go) return
  }
  await auditAirspaceApi(row.id, result, opinion)
  ElMessage.success(result === 1 ? '已通过并生效' : '已驳回，空域置为失效')
  await load()
}

async function toggleStatus(row: AirspaceAdminVO) {
  const next = row.status === 1 ? 2 : 1
  await updateAirspaceApi(row.id, { status: next })
  ElMessage.success(next === 1 ? '已启用' : '已停用')
  await load()
}

// ---------------- 新建 / 编辑 ----------------
const dlg = ref(false)
const editId = ref<number | null>(null)
const controlRange = ref<[string, string] | null>(null)
const form = reactive<any>({
  airspaceName: '', airspaceType: 2, altitudeMin: 0, altitudeMax: 120, regionName: '',
  controlReason: '', west: 120.9, east: 120.95, south: 27.92, north: 27.96,
})

const boundsHint = computed(() =>
  `矩形范围：经 ${form.west}~${form.east}，纬 ${form.south}~${form.north}`)

// 地图顶点：真正决定边界的东西。以前编辑时把多边形压成外接矩形再存回去，
// 斜边空域每编辑一次就退化成一个方框，判定范围被悄悄改掉。
const verts = ref<{ lng: number; lat: number }[]>([])
const drawnEnough = computed(() => verts.value.length >= 3)
const geomHint = computed(() => {
  if (!drawnEnough.value) return `尚未绘制顶点，将按矩形范围保存`
  const lngs = verts.value.map((v) => v.lng), lats = verts.value.map((v) => v.lat)
  return `${verts.value.length} 个顶点，范围 经 ${Math.min(...lngs).toFixed(4)}~${Math.max(...lngs).toFixed(4)}`
    + `，纬 ${Math.min(...lats).toFixed(4)}~${Math.max(...lats).toFixed(4)}`
})

const drawEl = ref<HTMLElement | null>(null)
const mapError = ref('')
let AMap: any = null
let drawMap: any = null
let draft: any = null

async function initDrawMap() {
  if (!drawEl.value) return
  destroyDrawMap()
  mapError.value = ''
  try { AMap = await loadAmap() } catch {
    mapError.value = amapError.value || '地图加载失败'
    return
  }
  const lngs = verts.value.map((v) => v.lng)
  const center: [number, number] = lngs.length
    ? [lngs.reduce((a, b) => a + b, 0) / lngs.length,
       verts.value.reduce((a, v) => a + v.lat, 0) / verts.value.length]
    : [(form.west + form.east) / 2, (form.south + form.north) / 2]
  drawMap = new AMap.Map(drawEl.value, { zoom: 13, center, viewMode: '2D' })
  drawMap.addControl && drawMap.addControl(new AMap.Scale())
  drawMap.on('click', (e: any) => {
    verts.value.push({ lng: Number(e.lnglat.lng), lat: Number(e.lnglat.lat) })
    redrawDraft()
  })
  // 高德地图默认右键菜单是「放大」，这里借来做撤销上一点（与围栏绘制同一套手感）
  drawMap.on('contextmenu', () => { verts.value.pop(); redrawDraft() })
  redrawDraft()
  if (verts.value.length) drawMap.setFitView([draft], false, [40, 40, 40, 40])
}

function redrawDraft() {
  if (!drawMap || !AMap) return
  if (draft) { drawMap.remove(draft); draft = null }
  if (verts.value.length < 2) return
  draft = new AMap.Polygon({
    path: verts.value.map((v) => [v.lng, v.lat] as [number, number]),
    strokeColor: '#1F6FB2', strokeWeight: 3, fillColor: '#1F6FB2', fillOpacity: 0.2,
  })
  drawMap.add(draft)
}

function destroyDrawMap() {
  if (drawMap) { drawMap.destroy(); drawMap = null; draft = null }
}

function clearVerts() {
  verts.value = []
  redrawDraft()
}

function ringFrom(pts: { lng: number; lat: number }[]) {
  const r = pts.map((p) => [Number(p.lng.toFixed(6)), Number(p.lat.toFixed(6))])
  r.push([r[0][0], r[0][1]])
  return r
}

function bboxOf(ring: number[][]) {
  const lngs = ring.map((c) => c[0]), lats = ring.map((c) => c[1])
  return { west: Math.min(...lngs), east: Math.max(...lngs), south: Math.min(...lats), north: Math.max(...lats) }
}

function ringOf(w: number, e: number, s: number, n: number) {
  return [[w, s], [e, s], [e, n], [w, n], [w, s]]
}

function openCreate() {
  editId.value = null
  controlRange.value = null
  Object.assign(form, {
    airspaceName: '', airspaceType: 2, altitudeMin: 0, altitudeMax: 120, regionName: '',
    controlReason: '', west: 120.9, east: 120.95, south: 27.92, north: 27.96,
  })
  verts.value = []
  dlg.value = true
}

function openEdit(row: AirspaceAdminVO) {
  editId.value = row.id
  // 末点与首点重复（GeoJSON 闭合环），去掉后再作可编辑顶点；原来的外接矩形回填方式
  // 会把非矩形空域"编辑"成方框，这里改成原样带回。
  const ring = (row.geometry?.coordinates?.[0] || []).slice(0, -1)
  verts.value = ring.map((c) => ({ lng: c[0], lat: c[1] }))
  if (ring.length) Object.assign(form, bboxOf(ring))
  controlRange.value = row.controlStartAt && row.controlEndAt
    ? [`${row.controlStartAt}:00`, `${row.controlEndAt}:00`]
    : null
  Object.assign(form, {
    airspaceName: row.airspaceName, airspaceType: row.airspaceType,
    altitudeMin: row.altitudeMin ?? 0, altitudeMax: row.altitudeMax ?? 120,
    regionName: row.regionName || '', controlReason: row.controlReason || '',
  })
  dlg.value = true
}

async function save() {
  if (!String(form.airspaceName || '').trim()) { ElMessage.warning('请填写空域名称'); return }
  if (form.airspaceType === 4 && !controlRange.value) { ElMessage.warning('临时管制必须填管制时段'); return }
  if (!drawnEnough.value && !(form.west < form.east && form.south < form.north)) {
    ElMessage.warning('地图不可用时，矩形范围需满足西<东、南<北'); return
  }
  const ring = drawnEnough.value ? ringFrom(verts.value) : ringOf(form.west, form.east, form.south, form.north)
  const geometry = { type: 'Polygon' as const, coordinates: [ring] }
  const payload: AirspacePayload = {
    airspaceName: form.airspaceName, airspaceType: form.airspaceType,
    altitudeMin: form.altitudeMin, altitudeMax: form.altitudeMax,
    regionName: form.regionName, controlReason: form.controlReason, geometry,
  }
  if (form.airspaceType === 4) {
    payload.controlStartAt = controlRange.value?.[0] || null
    payload.controlEndAt = controlRange.value?.[1] || null
  }
  saving.value = true
  try {
    if (editId.value) {
      const r = await updateAirspaceApi(editId.value, payload)
      ElMessage.success(r.auditStatus === 0 ? '边界/类型已变更，重新进入待审核' : '空域已更新')
    } else {
      await createAirspaceApi(payload)
      ElMessage.success('已提交审核，审核通过后生效')
    }
    dlg.value = false
    await load()
  } finally { saving.value = false }
}

async function doDel(row: AirspaceAdminVO) {
  const go = await ElMessageBox.confirm(
    `确认下架空域「${row.airspaceName}」？下架后不再参与航线合规判定与计划自动校验。`,
    '空域下架', { type: 'warning' }).catch(() => null)
  if (!go) return
  await deleteAirspaceApi(row.id)
  ElMessage.success('已下架')
  await load()
}

onBeforeUnmount(destroyDrawMap)
onMounted(load)
</script>

<style lang="scss" scoped>
.page { display: block; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px; flex-wrap: wrap; }
.stat { display: flex; gap: 18px; }
.stat-item label { display: block; font-size: 11.5px; color: var(--ts-text-secondary); }
.stat-item b { font-size: 17px; color: var(--ts-primary-dark); }
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.syncmsg { margin: 10px 0; }
.table { padding: 6px; }
.opinion { font-size: 11.5px; color: var(--ts-text-secondary); }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.note { margin: 10px 2px 0; line-height: 1.6; }
.bounds { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; }
.bounds span { font-size: 12px; color: var(--ts-text-regular); display: flex; align-items: center; gap: 6px; }
.dlg-body { display: flex; gap: 14px; align-items: flex-start; }
.mapcol { width: 380px; flex: none; }
.draw-map { width: 100%; height: 330px; border-radius: 4px; }
</style>
