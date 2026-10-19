<template>
  <div v-loading="loading">
    <el-page-header content="订单详情" @back="$router.back()" class="ts-card" style="padding: 12px 16px" />

    <el-row :gutter="12" style="margin-top: 12px">
      <el-col :span="14">
        <div class="ts-card">
          <div class="card-head">
            <span class="card-title">订单 {{ order?.orderNo }}</span>
            <el-tag v-if="order" :type="ORDER_STATUS_MAP[order.status]?.type" effect="light">
              {{ order.statusName }}
            </el-tag>
          </div>
          <el-descriptions :column="2" border size="small" style="margin-top: 12px">
            <el-descriptions-item label="寄件人">{{ order?.senderName }} {{ order?.senderPhone }}</el-descriptions-item>
            <el-descriptions-item label="寄件地址">{{ order?.senderAddress }}</el-descriptions-item>
            <el-descriptions-item label="收件人">{{ order?.receiverName }} {{ order?.receiverPhone }}</el-descriptions-item>
            <el-descriptions-item label="收件地址">{{ order?.receiverAddress }}</el-descriptions-item>
            <el-descriptions-item label="物品">
              {{ order?.goodsType }}<template v-if="order?.goodsName"> / {{ order.goodsName }}</template>
            </el-descriptions-item>
            <el-descriptions-item label="重量 / 数量">
              {{ order?.weightKg }}kg / {{ order?.quantity ?? 1 }} 件
              <el-tag v-if="order?.deliveryType === 2" size="small" type="danger" effect="plain">加急</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="支付方式">{{ order?.payMethodName || '—' }}</el-descriptions-item>
            <el-descriptions-item label="下单时间" class-name="num">{{ order?.createdAt }}</el-descriptions-item>
            <el-descriptions-item v-if="order?.cancelReason" label="取消原因" :span="2">
              <span class="danger">{{ order.cancelReason }}</span>
            </el-descriptions-item>
            <el-descriptions-item v-if="order?.remark" label="备注" :span="2">{{ order.remark }}</el-descriptions-item>
          </el-descriptions>

          <div class="fee-row">
            <span>基础运费 <b class="num">{{ yuan(order?.baseFee) }}</b></span>
            <span>空域服务费 <b class="num">{{ yuan(order?.airspaceFee) }}</b></span>
            <span>加急费 <b class="num">{{ yuan(order?.urgentFee) }}</b></span>
            <span class="total">应收 <b class="num">{{ yuan(order?.payAmount) }}</b></span>
          </div>

          <div class="actions">
            <template v-if="order?.status === 1">
              <el-button type="primary" @click="accept">接单</el-button>
              <el-button @click="reject">拒单</el-button>
            </template>
            <el-button
              v-if="order?.status === 2 && userStore.hasPerm('dispatch:plan')"
              type="warning"
              @click="goBoard"
            >去调度</el-button>
            <el-button v-if="plan" @click="$router.push(`/admin/dispatch/plans/${plan.id}`)">
              查看飞行计划 {{ plan.planNo }}
            </el-button>
          </div>
        </div>
      </el-col>

      <el-col :span="10">
        <div class="ts-card">
          <div class="card-head"><span class="card-title">航线与空域</span></div>
          <div v-if="mapError" class="map-fallback">
            <el-alert :title="mapError" type="warning" :closable="false" />
            <p class="sub">寄件坐标：<span class="num">{{ order?.senderLng }}, {{ order?.senderLat }}</span></p>
            <p class="sub">收件坐标：<span class="num">{{ order?.receiverLng }}, {{ order?.receiverLat }}</span></p>
          </div>
          <div v-show="!mapError" ref="mapEl" class="map-box" />
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getOrderApi, acceptOrderApi, rejectOrderApi, type OrderVO } from '@/api/order'
import { listPlansApi, listAirspacesApi, type FlightPlanVO, type AirspaceVO } from '@/api/dispatch'
import { ORDER_STATUS_MAP, yuan } from '@/constants/dict'
import { createDispatchMap, amapError, type NamedPoint } from '@/composables/useAmap'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const id = Number(route.params.id)
const order = ref<OrderVO | null>(null)
const plan = ref<FlightPlanVO | null>(null)
const loading = ref(true)
const mapEl = ref<HTMLElement | null>(null)
const mapError = ref('')
let mapInst: { destroy: () => void } | null = null

async function accept() {
  await ElMessageBox.confirm(`确认承接订单 ${order.value?.orderNo}？`, '商家接单', { type: 'info' })
  await acceptOrderApi(id)
  ElMessage.success('已接单')
  loadAll()
}

async function reject() {
  const { value } = await ElMessageBox.prompt('请填写拒单原因', '商家拒单', {
    inputValidator: (v) => (v && v.trim() ? true : '原因不能为空'),
  })
  await rejectOrderApi(id, value!)
  ElMessage.success('已拒单并退款（Mock）')
  loadAll()
}

function goBoard() {
  router.push({ path: '/admin/dispatch/board', query: { orderId: String(id) } })
}

async function loadAll() {
  loading.value = true
  try {
    order.value = await getOrderApi(id)
    const plans = await listPlansApi({ pageSize: 5, keyword: order.value.orderNo })
    plan.value = plans.list.find((p) => p.orderId === id) || null
    await renderMap()
  } finally {
    loading.value = false
  }
}

async function renderMap() {
  if (!order.value || !mapEl.value) return
  let airspaces: AirspaceVO[] = []
  try {
    airspaces = (await listAirspacesApi()).list
  } catch { /* 空域失败不阻塞 */ }
  const sender: NamedPoint = { lng: order.value.senderLng, lat: order.value.senderLat }
  const receiver: NamedPoint = { lng: order.value.receiverLng, lat: order.value.receiverLat }
  const markers: Record<string, NamedPoint> = {
    sender: { ...sender, name: order.value.senderAddress },
    receiver: { ...receiver, name: order.value.receiverAddress },
  }
  if (plan.value) {
    const p = plan.value
    if (p.takeoffVertiportLng != null && p.takeoffVertiportLat != null) {
      markers.takeoff = { lng: p.takeoffVertiportLng, lat: p.takeoffVertiportLat, name: p.takeoffVertiportName || '起飞场' }
    }
    if (p.landingVertiportLng != null && p.landingVertiportLat != null) {
      markers.landing = { lng: p.landingVertiportLng, lat: p.landingVertiportLat, name: p.landingVertiportName || '降落场' }
    }
  }
  try {
    mapInst = await createDispatchMap(mapEl.value, {
      airspaces,
      route: plan.value?.route || [sender, receiver],
      routeReturn: plan.value?.routeReturn || [],
      markers,
    })
  } catch {
    mapError.value = amapError.value || '地图加载失败'
  }
}

onMounted(loadAll)
onBeforeUnmount(() => mapInst?.destroy())
</script>

<style lang="scss" scoped>
.card-head { display: flex; align-items: center; justify-content: space-between; }
.card-title { font-weight: 700; color: var(--ts-primary-dark); font-size: 15px; }
.fee-row { display: flex; gap: 22px; margin-top: 14px; color: var(--ts-text-regular); font-size: 13px; }
.fee-row .total { margin-left: auto; color: var(--ts-primary-dark); font-size: 15px; }
.actions { margin-top: 14px; display: flex; gap: 8px; flex-wrap: wrap; }
.map-box { width: 100%; height: 460px; border-radius: 4px; overflow: hidden; margin-top: 12px; }
.map-fallback { margin-top: 12px; }
.danger { color: var(--el-color-danger); }
.sub { color: var(--ts-text-secondary); font-size: 12px; }
</style>
