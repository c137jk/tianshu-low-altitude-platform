<template>
  <div class="board">
    <!-- 左：待调度订单池 / 在途（申报后订单去哪了一目了然） -->
    <div class="ts-card col col-left">
      <div class="col-head">
        <span class="card-title">{{ view === 'pool' ? '待调度订单池' : '在途任务' }}</span>
        <el-button text :icon="Refresh" @click="view === 'pool' ? loadPool() : loadTransit()" />
      </div>
      <el-radio-group v-model="view" size="small" style="margin: 8px 0" @change="onViewChange">
        <el-radio-button value="pool">待调度 {{ pool.length ? `(${pool.length})` : '' }}</el-radio-button>
        <el-radio-button value="transit">在途 {{ transit.length ? `(${transit.length})` : '' }}</el-radio-button>
      </el-radio-group>

      <template v-if="view === 'pool'">
        <el-input v-model="poolKeyword" placeholder="筛选订单号/收件人" size="small" clearable style="margin-bottom: 8px" />
        <div class="pool">
          <div
            v-for="o in filteredPool"
            :key="o.id"
            class="pool-item"
            :class="{ active: current?.id === o.id }"
            @click="selectOrder(o)"
          >
            <div class="pool-top">
              <span class="num pool-no">{{ o.orderNo }}</span>
              <el-tag size="small" type="warning" effect="plain">{{ o.statusName }}</el-tag>
            </div>
            <div class="pool-route">
              <span>{{ o.senderAddress || '寄件点' }}</span>
              <el-icon><Right /></el-icon>
              <span>{{ o.receiverAddress || '收件点' }}</span>
            </div>
            <div class="pool-meta">
              {{ o.goodsType }} · {{ o.weightKg }}kg
              <el-tag v-if="o.deliveryType === 2" size="small" type="danger" effect="plain">加急</el-tag>
              <span class="num fee">{{ yuan(o.payAmount) }}</span>
            </div>
          </div>
          <el-empty v-if="!filteredPool.length" description="暂无待调度订单" :image-size="70" />
        </div>
      </template>

      <!-- 提交申报后订单就从上面的池子里消失了，这里接着往下看：卡在哪一步、该谁动 -->
      <div v-else class="pool">
        <div v-for="p in transit" :key="p.id" class="pool-item transit" @click="goPlan(p)">
          <div class="pool-top">
            <span class="num pool-no">{{ p.planNo }}</span>
            <el-tag size="small" :type="PLAN_STATUS_MAP[p.status]?.type" effect="light">{{ p.statusName }}</el-tag>
          </div>
          <div class="pool-route">
            <span>{{ p.takeoffVertiportName }}</span>
            <el-icon><Right /></el-icon>
            <span>{{ p.landingVertiportName }}</span>
          </div>
          <div class="pool-meta"><span class="num">{{ p.orderNo }}</span> · {{ p.pilotName || '未指派机长' }}</div>
          <div class="next">
            <el-icon><InfoFilled /></el-icon>
            <span>{{ nextStep(p) }}</span>
          </div>
        </div>
        <el-empty v-if="!transit.length" description="没有在途任务" :image-size="70" />
      </div>
    </div>

    <!-- 中：调度地图 -->
    <div class="ts-card col col-map">
      <div class="col-head">
        <span class="card-title">航线与空域态势</span>
        <span class="legend">
          <i class="lg lg-red" />禁飞
          <i class="lg lg-yellow" />限飞
          <i class="lg lg-green" />可飞
          <i class="lg lg-gold" />载货航线
          <i class="lg lg-dash" />调机返场
        </span>
      </div>
      <div v-if="mapError" class="map-fallback">
        <el-alert :title="mapError" type="warning" :closable="false" />
        <p class="sub" v-if="current">寄 <span class="num">{{ current.senderLng }},{{ current.senderLat }}</span>
          → 收 <span class="num">{{ current.receiverLng }},{{ current.receiverLat }}</span></p>
      </div>
      <div v-show="!mapError" ref="mapEl" class="map-box" />
    </div>

    <!-- 右：调度方案面板 -->
    <div class="ts-card col col-right">
      <div class="col-head"><span class="card-title">调度方案</span></div>
      <div v-if="!current" class="hint">
        <el-icon class="hint-icon"><Position /></el-icon>
        <p>从左侧订单池选择一单<br />系统将自动推荐飞机、机长与起降场</p>
      </div>

      <div v-else v-loading="recLoading" class="panel">
        <el-alert
          v-for="(r, i) in rec?.reasons || []"
          :key="i" :title="r" type="error" :closable="false" show-icon style="margin-bottom: 8px"
        />
        <el-alert
          v-for="(w, i) in rec?.warnings || []"
          :key="'w' + i" :title="w" type="warning" :closable="false" show-icon style="margin-bottom: 8px"
        />

        <template v-if="rec && !rec.blocked">
          <el-form label-width="74px" size="small">
            <el-form-item label="飞行器">
              <el-select v-model="form.aircraftId" style="width: 100%" @change="check">
                <el-option
                  v-for="a in rec.options.aircraft"
                  :key="a.id"
                  :value="a.id"
                  :disabled="a.busy"
                  :label="`${a.aircraftNo} ${a.brand}${a.model}（载重${a.maxLoadKg}kg/续航${a.enduranceMin}分${a.busy ? '·占用中' : ''}）`"
                >
                  <div class="opt-row">
                    <span>{{ a.aircraftNo }} {{ a.brand }}{{ a.model }}</span>
                    <el-tag size="small" :type="a.busy ? 'info' : 'success'">
                      {{ a.busy ? '占用中' : a.statusName }}
                    </el-tag>
                  </div>
                </el-option>
              </el-select>
            </el-form-item>
            <el-form-item label="机长">
              <el-select v-model="form.pilotId" style="width: 100%">
                <el-option
                  v-for="p in rec.options.pilots"
                  :key="p.id" :value="p.id" :disabled="p.busy"
                  :label="`${p.realName}（${p.username}）${p.busy ? '·占用中' : ''}`"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="起飞场">
              <el-select v-model="form.takeoffVertiportId" style="width: 100%">
                <el-option v-for="v in rec.options.vertiports" :key="'t' + v.id" :value="v.id"
                  :label="`${v.stationName}（${v.occupiedCount}/${v.capacity}）`" />
              </el-select>
            </el-form-item>
            <el-form-item label="降落场">
              <el-select v-model="form.landingVertiportId" style="width: 100%">
                <el-option v-for="v in rec.options.vertiports" :key="'l' + v.id" :value="v.id"
                  :label="`${v.stationName}（${v.occupiedCount}/${v.capacity}）`" />
              </el-select>
            </el-form-item>
            <el-form-item label="飞行高度">
              <el-input-number v-model="form.altitudeM" :min="20" :max="altitudeCap" :step="10" controls-position="right" />
              <span class="unit">米<span v-if="routeInfo.altitudeLimitFrom"> · 本航线受{{ routeInfo.altitudeLimitFrom }}
                {{ routeInfo.altitudeMax }}m 限制</span></span>
            </el-form-item>
            <el-form-item label="巡航速度">
              <el-input-number v-model="form.cruiseSpeed" :min="6" :max="20" :step="1" controls-position="right" />
              <span class="unit">m/s</span>
            </el-form-item>
            <el-form-item label="任务名称">
              <el-input v-model="form.taskName" placeholder="如：瓯江口物流配送-001" />
            </el-form-item>
          </el-form>

          <div class="metrics">
            <div><label>航程</label><b class="num">{{ fmtDistance(routeInfo.distanceM) }}</b></div>
            <div><label>预计航时</label><b class="num">{{ fmtDuration(routeInfo.durationSec) }}</b></div>
            <div><label>建议电量</label><b class="num">{{ selectedAircraft?.needBatteryPercent ?? '—' }}%</b></div>
          </div>

          <div class="weather ts-card" v-if="rec.recommended.weather">
            <div class="w-head">
              <span>气象（Mock）</span>
              <el-tag size="small" :type="rec.recommended.weather.flyable ? 'success' : 'danger'">
                {{ rec.recommended.weather.flyable ? '适飞' : '不适飞' }}
              </el-tag>
            </div>
            <div class="w-grid">
              <span>{{ rec.recommended.weather.weatherDesc }}</span>
              <span>{{ rec.recommended.weather.windDirectionName }}风 {{ rec.recommended.weather.windSpeed }}m/s</span>
              <span>能见度 {{ (rec.recommended.weather.visibilityM / 1000).toFixed(1) }}km</span>
              <span>{{ rec.recommended.weather.temperature }}℃</span>
            </div>
          </div>

          <el-alert v-for="(c, i) in checks" :key="i" :title="c.text" :type="c.ok ? 'success' : 'error'"
            :closable="false" show-icon style="margin-bottom: 6px" />

          <div class="panel-actions">
            <el-button v-if="!savedPlan" type="primary" :disabled="!canSave || !!rec.blocked"
              :loading="saving" @click="savePlan">保存方案</el-button>
            <template v-else>
              <el-tag type="success" effect="light">方案 {{ savedPlan.planNo }} 已保存（待申报）</el-tag>
              <el-button type="warning" :loading="submitting" @click="submitPlan">提交空管申报</el-button>
            </template>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Right, Position, InfoFilled } from '@element-plus/icons-vue'
import { listOrdersApi, type OrderVO } from '@/api/order'
import {
  recommendApi, createPlanApi, submitPlanApi, listAirspacesApi, recalcRouteApi, listPlansApi,
  type RecommendResult, type FlightPlanVO, type AirspaceVO,
} from '@/api/dispatch'
import { yuan, fmtDistance, fmtDuration, PLAN_STATUS_MAP } from '@/constants/dict'
import { createDispatchMap, amapError, type NamedPoint } from '@/composables/useAmap'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const pool = ref<OrderVO[]>([])
const poolKeyword = ref('')
const view = ref<'pool' | 'transit'>('pool')
const transit = ref<FlightPlanVO[]>([])
const current = ref<OrderVO | null>(null)
const rec = ref<RecommendResult | null>(null)
const airspaces = ref<AirspaceVO[]>([])
const recLoading = ref(false)
const saving = ref(false)
const submitting = ref(false)
const savedPlan = ref<FlightPlanVO | null>(null)
const routeInfo = ref<{ distanceM: number; durationSec: number; altitudeMax?: number | null;
  altitudeLimitFrom?: string | null }>({ distanceM: 0, durationSec: 0 })
// 上限由服务端按航线算出来（分区准入 + 全局限高取最严），前端不再判一遍几何
const altitudeCap = computed(() => routeInfo.value.altitudeMax ?? 300)
const mapEl = ref<HTMLElement | null>(null)
const mapError = ref('')
let mapInst: { destroy: () => void } | null = null

const form = ref({
  aircraftId: null as number | null,
  pilotId: null as number | null,
  takeoffVertiportId: null as number | null,
  landingVertiportId: null as number | null,
  altitudeM: 100,
  cruiseSpeed: 12,
  taskName: '',
})

const filteredPool = computed(() => {
  const k = poolKeyword.value.trim()
  if (!k) return pool.value
  return pool.value.filter((o) => o.orderNo.includes(k) || o.receiverName.includes(k) || o.senderName.includes(k))
})

const selectedAircraft = computed(() =>
  rec.value?.options.aircraft.find((a) => a.id === form.value.aircraftId))

const checks = computed(() => {
  if (!rec.value || !current.value) return []
  const out: { ok: boolean; text: string }[] = []
  const a = selectedAircraft.value
  if (a) {
    out.push({ ok: !a.busy, text: a.busy ? `${a.aircraftNo} 正在执行其他任务` : `${a.aircraftNo} 当前空闲可用` })
    out.push({
      ok: Number(a.maxLoadKg) >= Number(current.value.weightKg),
      text: `载重校验：货物 ${current.value.weightKg}kg / 上限 ${a.maxLoadKg}kg`,
    })
    if (a.needBatteryPercent != null && a.battery != null) {
      out.push({
        ok: a.battery >= a.needBatteryPercent,
        text: `电量校验：当前 ${a.battery}% / 建议 ≥${a.needBatteryPercent}%`,
      })
    }
  }
  const p = rec.value.options.pilots.find((x) => x.id === form.value.pilotId)
  if (p) out.push({ ok: !p.busy, text: p.busy ? `${p.realName} 有在途任务` : `机长 ${p.realName} 可执飞` })
  return out
})

const canSave = computed(() =>
  !!form.value.aircraftId && !!form.value.pilotId &&
  !!form.value.takeoffVertiportId && !!form.value.landingVertiportId &&
  checks.value.every((c) => c.ok))

async function loadPool() {
  const res = await listOrdersApi({ page: 1, pageSize: 50, status: 2 })
  pool.value = res.list
  const qid = Number(route.query.orderId)
  if (qid) {
    const target = res.list.find((o) => o.id === qid)
    if (target) { await selectOrder(target); return }
  }
  // 默认选中第一单"可调度"的（航程/电量满足）；全部不可行时才展示阻断单
  for (const o of res.list) {
    const r = await selectOrder(o)
    if (r && !r.blocked) return
  }
  if (res.list[0]) await selectOrder(res.list[0])
}

async function selectOrder(o: OrderVO): Promise<RecommendResult | null> {
  current.value = o
  rec.value = null
  savedPlan.value = null
  mapError.value = ''
  recLoading.value = true
  try {
    rec.value = await recommendApi(o.id)
    const r = rec.value.recommended
    form.value = {
      aircraftId: r.aircraftId, pilotId: r.pilotId,
      takeoffVertiportId: r.takeoffVertiportId, landingVertiportId: r.landingVertiportId,
      altitudeM: r.altitudeM, cruiseSpeed: r.cruiseSpeed,
      taskName: `${o.goodsType}配送-${o.orderNo.slice(-6)}`,
    }
    routeInfo.value = {
      distanceM: r.distanceM, durationSec: r.durationSec,
      altitudeMax: r.altitudeMax, altitudeLimitFrom: r.altitudeLimitFrom,
    }
    await renderMap()
    return rec.value
  } catch (e: any) {
    // 409 这类真 HTTP 错误，axios 的 e.message 是"Request failed with status code 409"，
    // 人读不出所以然；后端信封里的 message 才是"载重不足/电量不够"这种可用原因。
    mapError.value = e?.response?.data?.message || e?.message || '推荐失败'
    return null
  } finally {
    recLoading.value = false
  }
}

async function renderMap() {
  if (!mapEl.value || !rec.value || !current.value) return
  mapInst?.destroy()
  const vps = rec.value.options.vertiports
  const vp = (id: number | null) => vps.find((v) => v.id === id)
  // 以表单当前选中的起降场为准（用户切换后地图跟着变）
  const t = vp(form.value.takeoffVertiportId) || vp(rec.value.recommended.takeoffVertiportId)
  const l = vp(form.value.landingVertiportId) || vp(rec.value.recommended.landingVertiportId)
  const sender: NamedPoint = { lng: current.value.senderLng, lat: current.value.senderLat, name: current.value.senderAddress }
  const receiver: NamedPoint = { lng: current.value.receiverLng, lat: current.value.receiverLat, name: current.value.receiverAddress }
  // 重新拼航线：起飞场 → 发货点 → 收货点 → 降落场
  const customRoute = ([
    t && { lng: Number(t.lng), lat: Number(t.lat) }, sender, receiver,
    l && { lng: Number(l.lng), lat: Number(l.lat) },
  ].filter(Boolean)) as { lng: number; lat: number }[]
  try {
    mapInst = await createDispatchMap(mapEl.value, {
      airspaces: airspaces.value,
      route: customRoute,
      routeReturn: [],
      markers: {
        takeoff: t ? { lng: Number(t.lng), lat: Number(t.lat), name: t.stationName } : undefined,
        landing: l ? { lng: Number(l.lng), lat: Number(l.lat), name: l.stationName } : undefined,
        sender, receiver,
      },
    })
  } catch {
    mapError.value = amapError.value || '地图加载失败'
  }
}

// 切换起降场 → 立刻重画航线
watch(
  () => [form.value.takeoffVertiportId, form.value.landingVertiportId],
  async () => {
    if (!rec.value || !current.value) return
    if (form.value.takeoffVertiportId && form.value.landingVertiportId) {
      try {
        const r = await recalcRouteApi({
          orderId: current.value.id,
          takeoffVertiportId: form.value.takeoffVertiportId,
          landingVertiportId: form.value.landingVertiportId,
        })
        routeInfo.value = {
          distanceM: r.distanceM, durationSec: r.durationSec,
          altitudeMax: r.altitudeMax, altitudeLimitFrom: r.altitudeLimitFrom,
        }
      } catch { /* 静默：地图仍按表单重画 */ }
    }
    await renderMap()
  },
)

function check() { /* 依赖 computed checks，切换飞机时刷新提示；占位便于模板绑定 */ }

async function savePlan() {
  if (!current.value) return
  saving.value = true
  try {
    savedPlan.value = await createPlanApi({
      orderId: current.value.id,
      aircraftId: form.value.aircraftId!,
      pilotId: form.value.pilotId!,
      takeoffVertiportId: form.value.takeoffVertiportId!,
      landingVertiportId: form.value.landingVertiportId!,
      altitudeM: form.value.altitudeM,
      cruiseSpeed: form.value.cruiseSpeed,
      taskName: form.value.taskName,
    })
    ElMessage.success('方案已保存（待申报）')
  } finally {
    saving.value = false
  }
}

async function submitPlan() {
  if (!savedPlan.value) return
  submitting.value = true
  try {
    const ack = await submitPlanApi(savedPlan.value.id)
    const plan = savedPlan.value
    await loadPool()
    await loadTransit()
    view.value = 'transit'      // 订单从池子里消失了，直接把他带到"在途"看下一棒在谁手里
    savedPlan.value = null
    current.value = null
    rec.value = null
    ElMessageBox.alert(
      `申报号 ${ack.applyNo || '—'}。订单已转「待审批」，接下来由空管在【空域合规 → 飞行计划审批】`
      + '通过，再由调度下发机长，机长在【我的任务】确认起飞后大屏才会出现飞机。'
      + '左侧「在途」里能看到它现在卡在哪一步。',
      `已提交空管申报 · ${plan.planNo}`,
      { confirmButtonText: '知道了', type: 'success' },
    )
  } finally {
    submitting.value = false
  }
}

/** 在途：待审批 / 已批准待执行 / 执行中，三棒接力各自卡在谁手里 */
async function loadTransit() {
  const res = await listPlansApi({ page: 1, pageSize: 50 })
  transit.value = res.list.filter((p) => p.status === 1 || p.status === 2 || p.status === 3)
}

function nextStep(p: FlightPlanVO): string {
  if (p.status === 1) {
    return userStore.hasPerm('approval:handle')
      ? '等空管审批 —— 你有审批权限，去【空域合规 → 飞行计划审批】'
      : '等空管审批（空域合规 → 飞行计划审批）'
  }
  if (p.status === 2) {
    return p.assignedAt ? '已下发，等机长在【我的任务】确认起飞' : '等调度下发机长（计划详情 → 下发）'
  }
  return '在飞中，仿真器按帧推进；机长可在【我的任务】确认降落送达'
}

function goPlan(p: FlightPlanVO) {
  if (p.status === 1 && userStore.hasPerm('approval:handle')) {
    router.push('/admin/airspace/approval')
    return
  }
  router.push(`/admin/dispatch/plans/${p.id}`)
}

function onViewChange() {
  if (view.value === 'transit') loadTransit()
}

onMounted(async () => {
  try {
    airspaces.value = (await listAirspacesApi()).list
  } catch { /* 空域不阻塞 */ }
  await loadPool()
})
onBeforeUnmount(() => mapInst?.destroy())
</script>

<style lang="scss" scoped>
.board {
  display: grid;
  grid-template-columns: 290px 1fr 350px;
  gap: 12px;
  height: calc(100vh - 56px - 32px);
}
.col { display: flex; flex-direction: column; overflow: hidden; padding: 12px; min-width: 0; }
.col-head { display: flex; align-items: center; justify-content: space-between; }
.card-title { font-weight: 700; color: var(--ts-primary-dark); }
.pool { overflow-y: auto; flex: 1; }
.pool-item {
  border: 1px solid var(--ts-border); border-radius: 5px; padding: 8px 10px; margin-bottom: 8px;
  cursor: pointer; transition: all .15s; background: #fff;
}
.pool-item:hover { border-color: var(--ts-gold); }
.pool-item.active { border-color: var(--ts-gold); background: rgba(200, 164, 92, .08); box-shadow: inset 3px 0 0 var(--ts-gold); }
/* 在途条目的"下一棒在谁手里"：只给文字不够醒目，用蓝底小条把人引到对应的页 */
.pool-item.transit { cursor: pointer; }
.pool-item.transit:hover { border-color: var(--ts-primary); }
.next {
  margin-top: 6px; padding: 4px 6px; border-radius: 3px; background: rgba(35, 64, 110, .07);
  display: flex; align-items: center; gap: 4px; font-size: 11.5px; color: var(--ts-primary-dark); line-height: 1.4;
}
.next .el-icon { flex: none; color: var(--ts-primary); }
.pool-top { display: flex; justify-content: space-between; align-items: center; }
.pool-no { font-size: 12px; color: var(--ts-primary); }
.pool-route { display: flex; align-items: center; gap: 4px; font-size: 12.5px; margin: 6px 0; color: var(--ts-text-regular); }
.pool-route .el-icon { color: var(--ts-gold); flex: none; }
.pool-route span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pool-meta { font-size: 12px; color: var(--ts-text-secondary); display: flex; align-items: center; gap: 6px; }
.pool-meta .fee { margin-left: auto; color: var(--ts-primary-dark); font-weight: 600; }
.map-box { flex: 1; border-radius: 4px; overflow: hidden; margin-top: 8px; }
.map-fallback { margin-top: 10px; }
.legend { font-size: 12px; color: var(--ts-text-secondary); display: flex; align-items: center; gap: 4px; }
.lg { display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin: 0 2px 0 8px; }
.lg-red { background: rgba(192, 57, 43, .25); border: 1px solid #C0392B; }
.lg-yellow { background: rgba(211, 154, 43, .25); border: 1px solid #D39A2B; }
.lg-green { background: rgba(46, 125, 82, .25); border: 1px solid #2E7D52; }
.lg-gold { background: #C8A45C; }
.lg-dash { width: 14px; height: 0; border-top: 3px dashed rgba(200, 164, 92, .6); border-radius: 0; align-self: center; }
.hint { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--ts-text-secondary); }
.hint-icon { font-size: 40px; color: var(--ts-gold); margin-bottom: 10px; }
.panel { overflow-y: auto; flex: 1; margin-top: 8px; }
.unit { margin-left: 6px; color: var(--ts-text-secondary); font-size: 12px; }
.metrics { display: flex; gap: 8px; margin: 4px 0 10px; }
.metrics div {
  flex: 1; background: var(--ts-bg); border: 1px solid var(--ts-border); border-radius: 4px;
  padding: 8px; text-align: center;
}
.metrics label { display: block; font-size: 11px; color: var(--ts-text-secondary); }
.metrics b { font-size: 15px; color: var(--ts-primary-dark); }
.weather { padding: 10px; background: var(--ts-bg); margin-bottom: 10px; }
.w-head { display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; color: var(--ts-primary-dark); }
.w-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; font-size: 12px; color: var(--ts-text-regular); margin-top: 6px; }
.panel-actions { display: flex; align-items: center; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
.opt-row { display: flex; align-items: center; justify-content: space-between; width: 100%; }
.sub { color: var(--ts-text-secondary); font-size: 12px; }
</style>
