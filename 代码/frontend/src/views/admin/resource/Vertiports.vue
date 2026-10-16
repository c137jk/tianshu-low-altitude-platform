<template>
  <div class="ts-card pad" v-loading="loading">
    <el-tabs v-model="tab">
      <!-- 需求 4.2.3 场站管理：起降/换电场站台账（地图标点 + 容量 + 充电桩） -->
      <el-tab-pane label="场站台账" name="vp">
        <div class="bar">
          <el-button v-if="isAdmin" type="primary" size="small" @click="openD()">+ 新建起降场站</el-button>
          <span class="muted">开放 {{ openCount }} / 维护 {{ maintCount }} / 关闭 {{ closedCount }}　·　机位 {{ capTotal }}，充电桩可用 {{ chargerAvail }}/{{ chargerTotal }}</span>
        </div>
        <div class="grid">
          <div class="cards">
            <el-card v-for="v in vps" :key="v.id" shadow="hover" class="vpc">
              <div class="vpc-top">
                <b>{{ v.stationName }}</b>
                <el-tag size="small" :type="VP_TAG[v.status] || 'info'">{{ v.statusName }}</el-tag>
              </div>
              <div class="vpc-sub">{{ v.typeName }} · {{ v.stationNo }}</div>
              <div class="vpc-sub">{{ v.address || '—' }} <span class="num">({{ v.lng }},{{ v.lat }})</span></div>
              <div class="vpc-line">
                <span>机位占用</span>
                <el-progress :percentage="pct(v.occupiedCount, v.capacity)" :stroke-width="8"
                  :status="v.occupiedCount >= v.capacity ? 'exception' : 'success'" />
                <span class="num">{{ v.occupiedCount }}/{{ v.capacity }}</span>
              </div>
              <div class="vpc-line">
                <span>充电桩</span>
                <el-progress :percentage="pct(v.chargerAvailable, v.chargerTotal)" :stroke-width="8" />
                <span class="num">{{ v.chargerAvailable }}/{{ v.chargerTotal }}</span>
              </div>
              <div v-if="isAdmin" class="vpc-acts">
                <el-button size="small" link type="primary" @click="editVp(v)">编辑</el-button>
                <el-button size="small" link :type="v.status === 1 ? 'warning' : 'success'" @click="toggleVp(v)">
                  {{ v.status === 1 ? '转维护' : '恢复开放' }}
                </el-button>
                <el-button size="small" link type="danger" @click="delVp(v)">删除</el-button>
              </div>
            </el-card>
          </div>
          <div class="mapcol">
            <div ref="mapEl" class="map-box" />
            <p v-if="mapError" class="muted">{{ mapError }}</p>
          </div>
        </div>
      </el-tab-pane>

      <!-- 场站使用统计：以场站维度看驻场飞行器与健康度分布（需求 4.2.3「使用统计」） -->
      <el-tab-pane label="使用统计" name="use">
        <div v-for="v in vps" :key="v.id" class="station-use">
          <div class="su-head">
            <b>{{ v.stationName }}</b>
            <span class="muted">{{ v.typeName }} · {{ v.statusName }} · 在场 {{ (byStation[v.stationName] || []).length }} 架</span>
          </div>
          <el-table :data="byStation[v.stationName] || []" size="small" stripe>
            <el-table-column prop="aircraftNo" label="机号" width="130" class-name="num" />
            <el-table-column prop="model" label="机型" min-width="150" />
            <el-table-column prop="statusName" label="状态" width="100" />
            <el-table-column label="电量" width="170">
              <template #default="{ row }">
                <!-- 带 status 的 el-progress 会把百分比换成状态图标，读数单独写出来 -->
                <div class="bat">
                  <el-progress class="bat-bar" :percentage="Math.round(row.battery)" :stroke-width="8" :show-text="false"
                    :status="row.battery < 20 ? 'exception' : row.battery < 50 ? 'warning' : 'success'" />
                  <span class="num" :class="{ 'bat-low': row.battery < 20 }">{{ Math.round(row.battery) }}%</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="healthScore" label="健康分" width="90" class-name="num" />
            <el-table-column prop="taskCount" label="累计任务" width="90" class-name="num" />
            <el-table-column prop="totalFlightMin" label="累计飞行(min)" width="120" class-name="num" />
            <template #empty><span class="muted">暂无驻场飞行器</span></template>
          </el-table>
        </div>
        <p class="muted">
          机位与时段的预约冲突校验在「场站预约」页签里（M-55）；这里统计的是当前驻场分布，不含预约占位。
        </p>
      </el-tab-pane>

      <!-- 场站预约（M-55）：商家申请机位 → 平台侧审核放行 → 回执释放 -->
      <el-tab-pane label="场站预约" name="rsv">
        <div class="bar">
          <el-button v-if="canApply" type="primary" size="small" @click="openRsv">+ 申请机位</el-button>
          <el-select v-model="rsvFilter.stationId" size="small" placeholder="全部场站" clearable style="width:180px"
            @change="loadRsv">
            <el-option v-for="v in vps" :key="v.id" :value="v.id" :label="v.stationName" />
          </el-select>
          <el-select v-model="rsvFilter.status" size="small" placeholder="全部状态" clearable style="width:120px"
            @change="loadRsv">
            <el-option v-for="(n, k) in RSV_STATUS" :key="k" :value="Number(k)" :label="n" />
          </el-select>
          <el-date-picker v-model="rsvFilter.date" type="date" size="small" placeholder="预约日期"
            value-format="YYYY-MM-DD" clearable style="width:150px" @change="loadRsv" />
          <el-button size="small" @click="loadRsv">刷新</el-button>
          <span class="muted">待审 {{ rsvRows.filter(r => r.status === 1).length }} 条 · 商家只看本商家，平台侧看全部</span>
        </div>
        <el-table :data="rsvRows" size="small" stripe v-loading="rsvLoading">
          <el-table-column prop="reservationNo" label="预约号" width="150" class-name="num" />
          <el-table-column prop="stationName" label="场站" min-width="140" />
          <el-table-column prop="merchantName" label="申请商家" min-width="150" />
          <el-table-column prop="purposeName" label="用途" width="70" />
          <el-table-column label="时段" width="170">
            <template #default="{ row }"><span class="num">{{ slotText(row) }}</span></template>
          </el-table-column>
          <el-table-column label="机号" width="120">
            <template #default="{ row }"><span class="num">{{ row.aircraftNo || '—' }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="RSV_TAG[row.status] || 'info'">{{ row.statusName }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="auditOpinion" label="审核意见" min-width="140">
            <template #default="{ row }">{{ row.auditOpinion || '—' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <template v-if="canAudit && row.status === 1">
                <el-button size="small" link type="success" @click="rsvAudit(row, 2)">放行</el-button>
                <el-button size="small" link type="danger" @click="rsvAudit(row, 3)">驳回</el-button>
              </template>
              <el-button v-if="canApply && (row.status === 1 || row.status === 2)" size="small" link
                @click="rsvCancel(row)">取消</el-button>
              <el-button v-if="canApply && row.status === 2 && hasEnded(row)" size="small" link type="primary"
                @click="rsvFinish(row)">回执完成</el-button>
            </template>
          </el-table-column>
          <template #empty><span class="muted">暂无预约记录</span></template>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dlgRsv" title="申请场站机位" width="480px">
      <el-form :model="rsvForm" label-width="90px" size="small">
        <el-form-item label="场站">
          <el-select v-model="rsvForm.stationId" placeholder="选择场站" style="width:100%">
            <el-option v-for="v in vps" :key="v.id" :value="v.id"
              :label="`${v.stationName}（${v.statusName}·机位${v.capacity}/充电桩${v.chargerTotal}）`"
              :disabled="v.status !== 1" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="isAdmin" label="申请商家">
          <el-select v-model="rsvForm.merchantId" placeholder="平台侧代商家预约必选" style="width:100%">
            <el-option v-for="m in merchants" :key="m.id" :value="m.id" :label="m.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="用途">
          <el-radio-group v-model="rsvForm.purpose">
            <el-radio :value="1">起降</el-radio>
            <el-radio :value="2">换电</el-radio>
            <el-radio :value="3">充电</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="开始时间">
          <el-date-picker v-model="rsvForm.startAt" type="datetime" placeholder="预约开始时间"
            value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" />
        </el-form-item>
        <el-form-item label="时长">
          <el-select v-model="rsvForm.durationMin" style="width:100%">
            <el-option v-for="d in RSV_DURATIONS" :key="d" :value="d" :label="`${d} 分钟`" />
          </el-select>
        </el-form-item>
        <el-form-item label="飞行器">
          <el-select v-model="rsvForm.aircraftId" placeholder="可不填" clearable style="width:100%">
            <el-option v-for="a in myAircraft" :key="a.id" :value="a.id"
              :label="`${a.aircraftNo} ${a.model || ''}`" />
          </el-select>
        </el-form-item>
        <p class="muted">待审与已通过都占机位：同一时段同类机位约满时会被直接挡回，审核放行时还会再核一次。</p>
      </el-form>
      <template #footer>
        <el-button size="small" @click="dlgRsv = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="saveRsv">提交申请</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dlgVp" :title="editingId ? '编辑场站' : '新建起降场站'" width="640px" @opened="drawVpMap">
      <div class="dlg-body">
        <div ref="vpMapEl" class="pick-map" />
        <el-form :model="form" label-width="80px" size="small" class="form">
          <el-form-item label="名称"><el-input v-model="form.stationName" /></el-form-item>
          <el-form-item label="类型">
            <el-select v-model="form.stationType" :disabled="!!editingId" style="width:100%">
              <el-option :value="1" label="起降场" />
              <el-option :value="2" label="换电站" />
              <el-option :value="3" label="综合站" />
            </el-select>
          </el-form-item>
          <el-form-item label="地址"><el-input v-model="form.address" :disabled="!!editingId" /></el-form-item>
          <el-form-item label="经度"><el-input v-model.number="form.lng" :disabled="!!editingId" /></el-form-item>
          <el-form-item label="纬度"><el-input v-model.number="form.lat" :disabled="!!editingId" /></el-form-item>
          <el-form-item label="机位"><el-input-number v-model.number="form.capacity" :min="1" :max="40" /></el-form-item>
          <el-form-item label="充电桩"><el-input-number v-model.number="form.chargerTotal" :min="0" :max="40" /></el-form-item>
          <el-form-item v-if="editingId" label="状态">
            <el-select v-model="form.status" style="width:100%">
              <el-option :value="1" label="开放" />
              <el-option :value="2" label="维护中" />
              <el-option :value="3" label="关闭" />
            </el-select>
          </el-form-item>
          <p v-if="!editingId" class="muted">← 地图上点一下选位置（地址自动逆地理回填）</p>
        </el-form>
      </div>
      <template #footer>
        <el-button size="small" @click="dlgVp = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="saveD">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
import {
  auditReservationApi, cancelReservationApi,
  createReservationApi, createVertiportApi, finishReservationApi,
  listAircraftApi, listMerchantsApi, listReservationsApi, listVertiportsApi, updateVertiportApi,
  type AircraftVO, type ReservationVO, type VertiportVO,
} from '@/api/resource'
import { loadAmap, amapError } from '@/composables/useAmap'
import { useUserStore } from '@/stores/user'

const VP_TAG: Record<number, 'success' | 'warning' | 'info'> = { 1: 'success', 2: 'warning', 3: 'info' }

const userStore = useUserStore()
// 场站是平台侧资产：后端 _platform_only 认的是 user_type（4 平台/5 空管/6 运维），
// 之前这里只看 platform_admin 角色，平台运维的"编辑/转维护"按钮直接消失。
const isAdmin = computed(() => userStore.isPlatform)

const tab = ref('vp')
const vps = ref<VertiportVO[]>([])
const aircraft = ref<AircraftVO[]>([])
const loading = ref(true)
const saving = ref(false)
const mapEl = ref<HTMLElement | null>(null)
const mapError = ref('')
let map: any = null

const openCount = computed(() => vps.value.filter((v) => v.status === 1).length)
const maintCount = computed(() => vps.value.filter((v) => v.status === 2).length)
const closedCount = computed(() => vps.value.filter((v) => v.status === 3).length)
const capTotal = computed(() => vps.value.reduce((s, v) => s + (v.capacity || 0), 0))
const chargerTotal = computed(() => vps.value.reduce((s, v) => s + (v.chargerTotal || 0), 0))
const chargerAvail = computed(() => vps.value.reduce((s, v) => s + (v.chargerAvailable || 0), 0))

const byStation = computed(() => {
  const m: Record<string, AircraftVO[]> = {}
  for (const a of aircraft.value) {
    const k = a.vertiportName || '未分配'
    ;(m[k] ||= []).push(a)
  }
  return m
})
function pct(n: number, total: number) {
  return total > 0 ? Math.round((n / total) * 100) : 0
}

async function loadAll() {
  loading.value = true
  try {
    const [vp, ac] = await Promise.all([listVertiportsApi(), listAircraftApi()])
    vps.value = vp.list
    aircraft.value = ac.list
  } finally { loading.value = false }
  await nextTick()
  await renderMap()
}

async function renderMap() {
  if (!mapEl.value) return
  try {
    const AMap = await loadAmap()
    if (map) map.destroy()
    map = new AMap.Map(mapEl.value, { zoom: 12.5, center: [120.93, 27.95], viewMode: '2D' })
    for (const v of vps.value) {
      const bg = v.status === 1 ? '#23406E' : v.status === 2 ? '#D39A2B' : '#9aa3b2'
      new AMap.Marker({
        position: [v.lng, v.lat], map, anchor: 'center',
        content: `<div style="padding:2px 8px;background:${bg};color:#fff;border:1px solid #c8a45c;
          border-radius:3px;font-size:12px;white-space:nowrap">${v.stationName}·${v.occupiedCount}/${v.capacity}</div>`,
      })
    }
    if (vps.value.length) map.setFitView()
  } catch {
    mapError.value = amapError.value || '地图加载失败'
  }
}

watch(tab, async (t) => {
  if (t === 'vp') { await nextTick(); await renderMap() }
  // 预约台账只在进页签时拉：和场站卡片同屏会各发一次请求，切来切去反而看不到新数据
  if (t === 'rsv') await loadRsv()
})

// ---------------- 新建 / 编辑 ----------------
const dlgVp = ref(false)
const editingId = ref<number | null>(null)
const form = ref<any>({
  stationName: '', stationType: 1, lng: 120.93, lat: 27.95, address: '',
  capacity: 4, chargerTotal: 2, status: 1,
})
const vpMapEl = ref<HTMLElement | null>(null)
let vpMap: any = null

function openD() {
  editingId.value = null
  form.value = { stationName: '', stationType: 1, lng: 120.93, lat: 27.95, address: '', capacity: 4, chargerTotal: 2, status: 1 }
  dlgVp.value = true
}
function editVp(v: VertiportVO) {
  editingId.value = v.id
  form.value = {
    stationName: v.stationName, stationType: v.stationType, lng: v.lng, lat: v.lat,
    address: v.address || '', capacity: v.capacity, chargerTotal: v.chargerTotal || 2, status: v.status,
  }
  dlgVp.value = true
}

async function drawVpMap() {
  if (!vpMapEl.value) return
  let AMap: any
  try { AMap = await loadAmap() } catch {
    ElMessage.warning(amapError.value || '地图不可用，可手工填经纬度')
    return
  }
  if (vpMap) vpMap.destroy()
  vpMap = new AMap.Map(vpMapEl.value, { zoom: 14, center: [form.value.lng || 120.93, form.value.lat || 27.95] })
  new AMap.Marker({ position: [form.value.lng, form.value.lat], map: vpMap, anchor: 'center' })
  if (editingId.value) return
  vpMap.on('click', (e: any) => {
    form.value.lng = Number(e.lnglat.lng.toFixed(6))
    form.value.lat = Number(e.lnglat.lat.toFixed(6))
    AMap.plugin('AMap.Geocoder', () => {
      new AMap.Geocoder({ city: '温州' }).getAddress(
        [form.value.lng, form.value.lat],
        (st: string, r: any) => {
          if (st === 'complete' && r?.regeocode) form.value.address = r.regeocode.formattedAddress
        })
    })
    drawVpMarker(AMap)
  })
}
function drawVpMarker(AMap: any) {
  if (!vpMap) return
  vpMap.clearMap()
  new AMap.Marker({ position: [form.value.lng, form.value.lat], map: vpMap, anchor: 'center' })
}

async function saveD() {
  if (!form.value.stationName) { ElMessage.warning('请填写场站名称'); return }
  saving.value = true
  try {
    if (editingId.value) await updateVertiportApi(editingId.value, form.value)
    else await createVertiportApi(form.value)
    ElMessage.success('已保存')
    editingId.value = null
    dlgVp.value = false
    await loadAll()
  } finally { saving.value = false }
}

async function toggleVp(v: VertiportVO) {
  await updateVertiportApi(v.id, { status: v.status === 1 ? 2 : 1 })
  ElMessage.success(v.status === 1 ? '已转为维护中' : '已恢复开放')
  await loadAll()
}

async function delVp(v: VertiportVO) {
  const go = await ElMessageBox.confirm(
    `确认删除场站「${v.stationName}」？在场飞行器与后续调度将失去该起降点。`, '删除场站', { type: 'warning' })
    .catch(() => null)
  if (!go) return
  await request(`/resource/vertiports/${v.id}`, { method: 'DELETE' })
  ElMessage.success('已删除')
  await loadAll()
}

// ---------------- 场站预约（M-55） ----------------
// 权限点拆开：申请/取消/回执是商家侧动作，放行/驳回只在平台侧（运维、平台管理员）。
const canApply = computed(() => userStore.hasPerm('resource:reservation'))
const canAudit = computed(() => userStore.hasPerm('resource:reservation:audit'))

const RSV_STATUS: Record<number, string> = { 1: '待审', 2: '已通过', 3: '已驳回', 4: '已完成', 5: '已取消' }
const RSV_TAG: Record<number, 'success' | 'warning' | 'danger' | 'info'> =
  { 1: 'warning', 2: 'success', 3: 'danger', 4: 'info', 5: 'info' }
const RSV_DURATIONS = [30, 60, 120, 240, 480]

const rsvRows = ref<ReservationVO[]>([])
const rsvLoading = ref(false)
const rsvFilter = ref<{ stationId?: number; status?: number; date?: string }>({})

async function loadRsv() {
  rsvLoading.value = true
  try {
    const r = await listReservationsApi(rsvFilter.value)
    rsvRows.value = r.list
  } finally { rsvLoading.value = false }
}

const pad2 = (n: number) => String(n).padStart(2, '0')
function fmtLocal(d: Date) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}:00`
}
// 结束时间只补时分：预约号已经带着日期，跨天时段（22:00~02:00）靠 8 小时上限那条规则挡掉
function slotText(r: ReservationVO) {
  return `${r.startAt.slice(5, 16)}~${r.endAt.slice(11, 16)}`
}
function hasEnded(r: ReservationVO) {
  return new Date(r.endAt.replace(' ', 'T')).getTime() <= Date.now()
}

const dlgRsv = ref(false)
const rsvForm = ref<any>({
  stationId: undefined, merchantId: undefined, purpose: 1, startAt: '', durationMin: 60, aircraftId: undefined,
})
const merchants = ref<{ id: number; name: string }[]>([])
const myAircraft = computed(() => {
  if (isAdmin.value) return aircraft.value
  const mid = userStore.userInfo?.merchantId
  return aircraft.value.filter((a) => a.merchantId === mid)
})

async function openRsv() {
  // 平台侧要代商家预约（后端按登录态判：商家取本商家，平台必须显式给），
  // 所以商家列表要先拉到手，否则第一次打开时默认值落空、非手选不可。
  if (isAdmin.value && !merchants.value.length) merchants.value = await listMerchantsApi()
  // 默认明天 09:00：后端的闸门是"不能预约已经开始的时段"，给"今天下一小时"最容易一填就过期
  const d = new Date(Date.now() + 24 * 3600 * 1000)
  d.setHours(9, 0, 0, 0)
  rsvForm.value = {
    stationId: vps.value.find((v) => v.status === 1)?.id,
    merchantId: merchants.value[0]?.id, purpose: 1, startAt: fmtLocal(d),
    durationMin: 60, aircraftId: undefined,
  }
  dlgRsv.value = true
}

async function saveRsv() {
  const f = rsvForm.value
  if (!f.stationId || !f.startAt) { ElMessage.warning('请选择场站与开始时间'); return }
  if (isAdmin.value && !f.merchantId) { ElMessage.warning('请选择申请商家'); return }
  const end = new Date(new Date(f.startAt.replace(' ', 'T')).getTime() + f.durationMin * 60000)
  saving.value = true
  try {
    await createReservationApi({
      stationId: f.stationId, purpose: f.purpose, startAt: f.startAt, endAt: fmtLocal(end),
      merchantId: isAdmin.value ? f.merchantId : undefined,
      aircraftId: f.aircraftId || undefined,
    })
    ElMessage.success('预约已提交，等待审核')
    dlgRsv.value = false
    await loadRsv()
  } finally { saving.value = false }
}

async function rsvAudit(row: ReservationVO, result: 2 | 3) {
  let opinion: string | undefined
  if (result === 3) {
    const r = await ElMessageBox.prompt('驳回原因会随站内信发给申请方', `驳回预约 ${row.reservationNo}`, {
      inputValidator: (v) => (!!v && v.trim().length > 0) || '请填写驳回原因',
    }).catch(() => null)
    if (!r) return
    opinion = (r.value as string).trim()
  }
  await auditReservationApi(row.id, result, opinion)
  ElMessage.success(result === 2 ? '已放行' : '已驳回')
  await loadRsv()
}

async function rsvCancel(row: ReservationVO) {
  const go = await ElMessageBox.confirm(
    `确认取消预约 ${row.reservationNo}（${row.stationName} ${slotText(row)}）？`, '取消预约', { type: 'warning' })
    .catch(() => null)
  if (!go) return
  await cancelReservationApi(row.id)
  ElMessage.success('预约已取消，机位已释放')
  await loadRsv()
}

async function rsvFinish(row: ReservationVO) {
  await finishReservationApi(row.id)
  ElMessage.success('已回执完成')
  await loadRsv()
}

onMounted(loadAll)
</script>

<style lang="scss" scoped>
.pad { padding: 10px 14px 14px; }
.bar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.grid { display: grid; grid-template-columns: minmax(320px, 46%) 1fr; gap: 12px; }
.cards { max-height: 560px; overflow-y: auto; padding-right: 4px; }
.vpc { margin-bottom: 10px; }
.vpc-top { display: flex; justify-content: space-between; align-items: center; }
.vpc-sub { font-size: 12px; color: var(--ts-text-secondary); margin-top: 5px; }
.vpc-line { display: flex; align-items: center; gap: 8px; font-size: 12px; margin-top: 6px; }
.vpc-line :deep(.el-progress) { flex: 1; }
.vpc-acts { margin-top: 8px; text-align: right; }
.mapcol { min-width: 0; }
.map-box { width: 100%; height: 520px; border-radius: 4px; }
.station-use { margin-bottom: 16px; }
.bat { display: flex; align-items: center; gap: 6px; }
.bat-bar { flex: 1; min-width: 60px; }
.bat-low { color: #c0392b; font-weight: 700; }
.su-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px; }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.dlg-body { display: flex; gap: 12px; }
.pick-map { width: 300px; height: 320px; border-radius: 4px; flex: none; }
.form { flex: 1; min-width: 0; }
</style>
