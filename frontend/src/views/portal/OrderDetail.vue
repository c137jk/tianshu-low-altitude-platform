<template>
  <div v-loading="loading">
    <el-page-header @back="$router.push('/portal/orders')" content="订单详情" style="margin-bottom:16px" />

    <template v-if="order">
      <!-- 状态头 -->
      <div class="ts-card status-head">
        <div>
          <div class="no-row">
            <span class="order-no">{{ order.orderNo }}</span>
            <el-tag :type="ORDER_STATUS_MAP[order.status]?.type">{{ order.statusName }}</el-tag>
          </div>
          <div class="time">下单时间：{{ order.createdAt }}</div>
        </div>
        <div class="actions">
          <template v-if="isMine">
            <el-button v-if="order.status === 0" type="primary" @click="cashierVisible = true">立即支付</el-button>
            <el-button v-if="order.status === 6" type="success" @click="onConfirm">确认收货</el-button>
            <el-button v-if="[0, 1].includes(order.status)" type="danger" plain @click="onCancel">取消订单</el-button>
          </template>
          <span v-else-if="[0, 1, 6].includes(order.status)" class="by-customer">支付、取消与确认收货由下单客户本人操作</span>
        </div>
      </div>

      <!-- 进度 -->
      <div class="ts-card" style="margin-top:16px">
        <el-alert v-if="order.status === 8" type="info" :closable="false"
          :title="`订单已取消：${order.cancelReason || '客户主动取消'}`" style="margin-bottom:12px" />
        <el-alert v-else-if="order.status === 0" type="warning" :closable="false"
          title="订单待支付，请在 30 分钟内完成支付" style="margin-bottom:12px" />
        <el-alert v-else-if="order.status === 5" type="success" :closable="false"
          title="无人机配送中——实时轨迹（WS）将在 W4 仿真器联调后接入" style="margin-bottom:12px" />
        <el-steps v-if="order.status !== 8" :active="stepIndex(order.status)" align-center finish-status="success">
          <el-step v-for="s in ORDER_STEPS" :key="s" :title="s" />
        </el-steps>
      </div>

      <el-row :gutter="16" style="margin-top:16px">
        <el-col :span="14">
          <div class="ts-card">
            <h3>配送航线</h3>
            <div ref="mapRef" class="map-box"></div>
            <el-alert v-if="mapError" type="warning" :closable="false"
              :title="mapError" style="margin-top:10px" />
          </div>
        </el-col>
        <el-col :span="10">
          <div class="ts-card info-card">
            <h3>寄收信息</h3>
            <div class="party"><span class="tag send">寄</span>
              {{ order.senderName }} {{ order.senderPhone }}<br />{{ order.senderAddress }}
            </div>
            <div class="party"><span class="tag recv">收</span>
              {{ order.receiverName }} {{ order.receiverPhone }}<br />{{ order.receiverAddress }}
            </div>
            <h3 style="margin-top:18px">物品信息</h3>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="类型">{{ order.goodsType }}{{ order.goodsName ? ' / ' + order.goodsName : '' }}</el-descriptions-item>
              <el-descriptions-item label="重量">{{ order.weightKg }} kg × {{ order.quantity }} 件</el-descriptions-item>
              <el-descriptions-item label="时效">{{ order.deliveryType === 2 ? '加急' : '普通' }}{{ order.expectAt ? ' / 预约 ' + order.expectAt : '' }}</el-descriptions-item>
              <el-descriptions-item v-if="order.remark" label="备注">{{ order.remark }}</el-descriptions-item>
            </el-descriptions>
            <h3 style="margin-top:18px">费用明细</h3>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="基础运费">{{ yuan(order.baseFee) }}</el-descriptions-item>
              <el-descriptions-item label="空域服务费">{{ yuan(order.airspaceFee) }}</el-descriptions-item>
              <el-descriptions-item label="加急费">{{ yuan(order.urgentFee) }}</el-descriptions-item>
              <!-- 用券的订单要把抵扣讲清楚：只看到实付变少、不知道减在哪，客户会以为运费涨价了 -->
              <el-descriptions-item v-if="order.discountAmount > 0" label="优惠券抵扣">
                <span class="off">-{{ yuan(order.discountAmount) }}</span>
                <span v-if="order.couponName" class="pay-method">（{{ order.couponName }}）</span>
              </el-descriptions-item>
              <el-descriptions-item label="实付">
                <span class="num">{{ yuan(order.payAmount || order.totalAmount) }}</span>
                <span v-if="order.payMethodName" class="pay-method">（{{ order.payMethodName }}）</span>
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </el-col>
      </el-row>
    </template>

    <!-- 收银台 -->
    <el-dialog v-model="cashierVisible" title="Mock 收银台（不产生真实扣款）" width="420px" align-center>
      <p class="amount">应付 <span class="num">{{ order ? yuan(order.totalAmount) : '' }}</span></p>
      <el-radio-group v-model="payMethod" style="display:flex;flex-direction:column;gap:12px;margin:16px 0">
        <el-radio v-for="m in PAY_METHODS" :key="m.value" :label="m.value" border>{{ m.label }}</el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="cashierVisible = false">取消</el-button>
        <el-button type="primary" :loading="paying" @click="onPay">确认支付</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getOrderApi, payOrderApi, cancelOrderApi, confirmOrderApi, type OrderVO,
} from '@/api/order'
import { ORDER_STATUS_MAP, ORDER_STEPS, PAY_METHODS, stepIndex, yuan } from '@/constants/dict'
import { loadAmap, createRouteMap, amapError } from '@/composables/useAmap'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)
const order = ref<OrderVO | null>(null)
// 与后端 pay_order/cancel_order/confirm_receipt 的 customer_id == user.id 同一口径：
// 商家员工从「我的订单」点进别人的单子时，这三个动作必然 403，不该给按钮。
const isMine = computed(() => !!order.value && order.value.customerId === userStore.userInfo?.id)
const mapRef = ref<HTMLElement>()
const mapError = ref('')
let mapCtrl: { destroy: () => void } | null = null
const cashierVisible = ref(false)
const payMethod = ref(1)
const paying = ref(false)

async function load() {
  loading.value = true
  try {
    order.value = await getOrderApi(route.params.id as string)
    await nextTick()
    if (mapRef.value && order.value) {
      try {
        await loadAmap()
        mapCtrl = await createRouteMap(
          mapRef.value,
          { lng: order.value.senderLng, lat: order.value.senderLat, address: order.value.senderAddress },
          { lng: order.value.receiverLng, lat: order.value.receiverLat, address: order.value.receiverAddress },
        )
      } catch {
        mapError.value = amapError.value
      }
    }
  } finally {
    loading.value = false
  }
}

async function onPay() {
  if (!order.value) return
  paying.value = true
  try {
    await payOrderApi(order.value.id, payMethod.value)
    ElMessage.success('支付成功（Mock）')
    cashierVisible.value = false
    load()
  } finally {
    paying.value = false
  }
}

async function onCancel() {
  let reason = '客户主动取消'
  try {
    const r = await ElMessageBox.prompt('请填写取消原因', '取消订单', { inputValue: reason })
    reason = r.value || reason
  } catch { return }
  await cancelOrderApi(order.value!.id, reason)
  ElMessage.success('订单已取消' + (order.value?.paid ? '，支付金额将原路退回（Mock）' : ''))
  load()
}

async function onConfirm() {
  await ElMessageBox.confirm('确认已收到货物？', '确认收货', { type: 'success' })
  await confirmOrderApi(order.value!.id)
  ElMessage.success('已确认收货，感谢使用')
  load()
}

onMounted(load)
onBeforeUnmount(() => mapCtrl?.destroy())
</script>

<style lang="scss" scoped>
.status-head { display: flex; justify-content: space-between; align-items: center; }
.order-no { font-weight: 700; color: var(--ts-primary-dark); font-size: 15px; margin-right: 10px; }
.time { font-size: 12px; color: var(--ts-text-secondary); margin-top: 6px; }
.by-customer { font-size: 12px; color: var(--ts-text-secondary); }
h3 { margin: 0 0 12px; color: var(--ts-primary-dark); font-size: 15px; }
.map-box { width: 100%; height: 380px; border-radius: 6px; border: 1px solid var(--ts-border); overflow: hidden; }
.party { font-size: 13px; line-height: 1.8; color: var(--ts-text-regular); margin-bottom: 10px; }
.tag {
  display: inline-block; width: 20px; height: 20px; border-radius: 50%; color: #fff;
  text-align: center; line-height: 20px; font-size: 12px; margin-right: 6px;
}
.tag.send { background: var(--ts-primary); }
.tag.recv { background: var(--ts-gold); }
.num { color: var(--ts-danger); font-size: 19px; font-weight: 700; }
.pay-method { font-size: 12px; color: var(--ts-text-secondary); }
.off { color: var(--ts-success, #17b978); font-weight: 700; }
.amount { text-align: center; margin: 8px 0; }
</style>
