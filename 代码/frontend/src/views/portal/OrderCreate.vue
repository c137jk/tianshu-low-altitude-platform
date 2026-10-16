<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="15">
        <!-- 地图选点 -->
        <div class="ts-card">
          <div class="card-head">
            <h3>地图选点</h3>
            <el-radio-group v-model="activeKind" size="small" @change="onKindChange">
              <el-radio-button label="sender">设置寄件点</el-radio-button>
              <el-radio-button label="receiver">设置收件点</el-radio-button>
            </el-radio-group>
          </div>
          <div ref="mapRef" class="map-box"></div>
          <el-alert
            v-if="mapError"
            type="warning" :closable="false" show-icon
            title="地图不可用，可手动填写经纬度（GCJ-02）后继续下单"
            style="margin-top:10px"
          >
            <template #default>{{ mapError }}</template>
          </el-alert>
          <div v-if="mapError" class="manual-coord">
            <el-input v-model.number="manualLng" placeholder="经度 如 120.930">
              <template #prepend>经度</template>
            </el-input>
            <el-input v-model.number="manualLat" placeholder="纬度 如 27.990">
              <template #prepend>纬度</template>
            </el-input>
            <el-button @click="manualLocate">定位到{{ activeKind === 'sender' ? '寄件' : '收件' }}点</el-button>
          </div>
          <p class="map-tip">在地图上点击设置当前点，标记可拖动微调；地址会自动逆地理回填，也可手动修改。</p>
        </div>

        <!-- 寄收件表单 -->
        <el-form :model="form" label-width="86px" class="ts-card" style="margin-top:16px">
          <template v-for="kind in kinds" :key="kind">
            <div class="group-title">{{ kind === 'sender' ? '寄件信息' : '收件信息' }}</div>
            <el-row :gutter="12">
              <el-col :span="12">
                <el-form-item label="姓名" required>
                  <el-input v-model="form[kind].name" :placeholder="`${kind === 'sender' ? '寄' : '收'}件人姓名`" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="手机号" required>
                  <el-input v-model="form[kind].phone" maxlength="11" placeholder="11 位手机号" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-form-item label="地址" required>
              <el-autocomplete
                v-model="form[kind].address"
                :placeholder="`输入${kind === 'sender' ? '寄' : '收'}件位置（如：温州商学院），点搜索结果自动定位`"
                :fetch-suggestions="poiSuggestions(kind)"
                value-key="value" clearable style="width:100%"
                @select="(i: any) => onPick(kind, i)"
              />
            </el-form-item>
            <el-form-item label="坐标">
              <el-input :model-value="coordText(kind)" readonly placeholder="请在地图上选点" />
            </el-form-item>
          </template>
        </el-form>

        <!-- 物品信息 -->
        <el-form :model="form" label-width="86px" class="ts-card" style="margin-top:16px">
          <div class="group-title">物品与时效</div>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="物品类型" required>
                <el-select v-model="form.goodsType" placeholder="请选择" style="width:100%">
                  <el-option v-for="g in GOODS_TYPES" :key="g" :label="g" :value="g" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="物品名称">
                <el-input v-model="form.goodsName" placeholder="如：合同文件" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="重量(kg)" required>
                <el-input-number v-model="form.weightKg" :min="0.1" :max="30" :precision="2" style="width:100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="配送时效">
                <el-radio-group v-model="form.deliveryType">
                  <el-radio :label="1">普通</el-radio>
                  <el-radio :label="2">加急（+50%）</el-radio>
                </el-radio-group>
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="预约时间">
            <el-date-picker
              v-model="form.expectAt" type="datetime" placeholder="留空 = 即时单"
              format="YYYY-MM-DD HH:mm" value-format="YYYY-MM-DDTHH:mm:ss" style="width:100%"
            />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="form.remark" type="textarea" :rows="2" maxlength="255" show-word-limit />
          </el-form-item>
        </el-form>
      </el-col>

      <!-- 右侧费用面板 -->
      <el-col :span="9">
        <div class="ts-card fee-panel">
          <h3>费用试算</h3>
          <el-alert v-if="quoteError" type="error" :closable="false" show-icon :title="quoteError" style="margin-bottom:12px" />
          <el-empty v-if="!quote && !quoteError" description="选好寄收点与重量后自动试算" :image-size="70" />
          <div v-else-if="quote" class="fee-body">
            <div class="fee-row"><span>航线距离</span><span>{{ (quote.distanceM / 1000).toFixed(2) }} km</span></div>
            <div class="fee-row"><span>预计航程</span><span>约 {{ Math.round(quote.estDurationSec / 60) }} 分钟</span></div>
            <div class="fee-row"><span>基础运费</span><span>{{ yuan(quote.baseFee) }}</span></div>
            <div class="fee-row"><span>空域服务费</span><span>{{ yuan(quote.airspaceFee) }}</span></div>
            <div class="fee-row" v-if="quote.urgentFee > 0"><span>加急费</span><span>{{ yuan(quote.urgentFee) }}</span></div>
            <div class="coupon-pick">
              <span>优惠券</span>
              <el-select
                v-model="couponId" size="small" clearable placeholder="不使用优惠券"
                style="width:190px" @change="doEstimate"
                :no-data-text="coupons.length ? '券包里没有可用的券' : '券包是空的，去「领券中心」领一张'"
              >
                <el-option
                  v-for="c in coupons" :key="c.id" :value="c.id"
                  :label="`${c.couponName} · ${c.ruleText}`" :disabled="!couponUsable(c)"
                />
              </el-select>
            </div>
            <div class="fee-row" v-if="quote.discountAmount > 0">
              <span>优惠券抵扣{{ quote.coupon?.name ? `（${quote.coupon.name}）` : '' }}</span>
              <span class="off">-{{ yuan(quote.discountAmount) }}</span>
            </div>
            <el-alert
              v-if="quote.crossLimitedZones.length"
              type="warning" :closable="false" show-icon
              :title="`航线穿越限飞区：${quote.crossLimitedZones.join('、')}，将随飞行计划申报`"
              style="margin:8px 0"
            />
            <div class="fee-total">
              <span>合计</span>
              <span class="total-val">
                <s v-if="quote.discountAmount > 0" class="orig">{{ yuan(quoteTotalBeforeCoupon) }}</s>
                <span class="num">{{ yuan(quote.totalAmount) }}</span>
              </span>
            </div>
            <div class="merchant">承接：{{ quote.merchantName }}</div>
          </div>
          <el-button
            type="primary" size="large" class="submit-btn"
            :disabled="!quote" :loading="submitting"
            @click="submitOrder"
          >提交订单</el-button>
          <p class="fee-note">提交后进入 Mock 收银台支付；禁飞区航线与禁运物品会被系统拦截。</p>
        </div>
      </el-col>
    </el-row>

    <!-- Mock 收银台 -->
    <el-dialog v-model="cashierVisible" title="Mock 收银台（不产生真实扣款）" width="420px" align-center>
      <div v-if="createdOrder" class="cashier">
        <p>订单号：{{ createdOrder.orderNo }}</p>
        <p class="amount">应付 <span class="num">{{ yuan(createdOrder.totalAmount) }}</span></p>
        <el-radio-group v-model="payMethod" style="display:flex;flex-direction:column;gap:12px;margin:16px 0">
          <el-radio v-for="m in PAY_METHODS" :key="m.value" :label="m.value" border>{{ m.label }}</el-radio>
        </el-radio-group>
      </div>
      <template #footer>
        <el-button @click="goDetailLater">暂不支付</el-button>
        <el-button type="primary" :loading="paying" @click="pay">确认支付</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  estimateApi, createOrderApi, payOrderApi,
  type EstimateResult, type OrderVO, type OrderPayload,
} from '@/api/order'
import { myCouponsApi, type MyCoupon } from '@/api/marketing'
import { GOODS_TYPES, PAY_METHODS, yuan } from '@/constants/dict'
import { loadAmap, createPickerMap, amapError } from '@/composables/useAmap'

const router = useRouter()
const kinds = ['sender', 'receiver'] as const
type Kind = (typeof kinds)[number]
const mapRef = ref<HTMLElement>()
let mapCtrl: { setActive: (k: 'sender' | 'receiver') => void; setPoint: any; destroy: () => void } | null = null
const activeKind = ref<'sender' | 'receiver'>('sender')
const mapError = ref('')
const manualLng = ref<number>()
const manualLat = ref<number>()

const form = reactive({
  sender: { name: '', phone: '', address: '', lng: 0, lat: 0 },
  receiver: { name: '', phone: '', address: '', lng: 0, lat: 0 },
  goodsType: '文件证件',
  goodsName: '',
  weightKg: 1,
  deliveryType: 1 as 1 | 2,
  expectAt: null as string | null,
  remark: '',
})

const quote = ref<EstimateResult | null>(null)
const quoteError = ref('')
const coupons = ref<MyCoupon[]>([])
const couponId = ref<number>()
const submitting = ref(false)
const cashierVisible = ref(false)
const createdOrder = ref<OrderVO | null>(null)
const payMethod = ref(1)
const paying = ref(false)
let timer: number | undefined

function coordText(kind: 'sender' | 'receiver') {
  const p = form[kind]
  return p.lng ? `${p.lng.toFixed(6)}, ${p.lat.toFixed(6)}` : ''
}

let autoCmp: any = null
function ensureAutoComplete(): Promise<any> {
  return new Promise((resolve) => {
    if (autoCmp) return resolve(autoCmp)
    const AMap = (window as any).AMap
    AMap.plugin('AMap.AutoComplete', () => {
      autoCmp = new AMap.AutoComplete({ city: '温州' })
      resolve(autoCmp)
    })
  })
}
async function queryPOI(kw: string, cb: (r: any[]) => void, kind: 'sender' | 'receiver') {
  if (!kw || kw.length < 2 || !(window as any).AMap) { cb([]); return }
  try {
    const ac = await ensureAutoComplete()
    ac.search(kw, (status: string, result: any) => {
      if (status !== 'complete' || !result.tips) { cb([]); return }
      cb(result.tips.filter((t: any) => t.location).map((t: any) => ({
        value: t.name, lng: t.location.lng, lat: t.location.lat, addr: (t.district || '') + (t.address || ''),
      })))
    })
  } catch { cb([]) }
}
/** 地址联想：模板里直接写带类型的箭头函数会被 vue 的表达式解析器卡住，抽成工厂函数 */
const poiSuggestions = (kind: 'sender' | 'receiver') =>
  (q: string, cb: (r: any[]) => void) => queryPOI(q, cb, kind)

function onPick(kind: 'sender' | 'receiver', tip: any) {
  form[kind].address = tip.value
  mapCtrl?.setPoint(kind, Number(tip.lng), Number(tip.lat), tip.addr || tip.value)
}

function onKindChange(k: string | number | boolean) {
  mapCtrl?.setActive(k as 'sender' | 'receiver')
}

function manualLocate() {
  if (!manualLng.value || !manualLat.value) {
    ElMessage.warning('请填写经纬度')
    return
  }
  mapCtrl?.setPoint(activeKind.value, Number(manualLng.value), Number(manualLat.value), '手动选点')
}

async function doEstimate() {
  const s = form.sender, r = form.receiver
  if (!s.lng || !r.lng || !form.weightKg) {
    quote.value = null
    quoteError.value = ''
    return
  }
  const body: Partial<OrderPayload> = {
    senderLng: s.lng, senderLat: s.lat, receiverLng: r.lng, receiverLat: r.lat,
    weightKg: form.weightKg, deliveryType: form.deliveryType,
    goodsType: form.goodsType, goodsName: form.goodsName,
  }
  const cid = couponToSend()
  if (cid) body.couponId = cid
  try {
    quote.value = await estimateApi(body)
    quoteError.value = ''
  } catch (e: any) {
    if (body.couponId) {
      // 券在别处被核销/下架/过期，只有服务端知道：摘掉券按原价再试一次，别让整块价格面板空掉
      couponId.value = undefined
      return doEstimate()
    }
    quote.value = null
    // 试算被服务端拒（如航线穿禁飞区返回 409）时，axios 的 e.message 只是
    // "Request failed with status code 409"，真正的原因在后端信封里。
    quoteError.value = e?.response?.data?.message || e?.message || '试算失败'
  }
}

const chosenCoupon = computed(() => coupons.value.find((c) => c.id === couponId.value) || null)

/** 抵扣前的原价：券只改 discountAmount / totalAmount，三项费用不受影响 */
const quoteTotalBeforeCoupon = computed(() => {
  const q = quote.value
  return q ? q.baseFee + q.airspaceFee + q.urgentFee : 0
})

/** 门槛按原价判，与服务端 usable() 同口径；服务端仍是最终裁决 */
function couponUsable(c: MyCoupon) {
  return !quote.value || quoteTotalBeforeCoupon.value + 0.001 >= c.threshold
}

function couponToSend(): number | undefined {
  const c = chosenCoupon.value
  return c && quoteTotalBeforeCoupon.value + 0.001 >= c.threshold ? c.id : undefined
}

// 改重量/时效把总价压到门槛以下时，选中的券会自动不可用（couponToSend 就不发了）。
// 但下拉框还显示着它，客户以为减了 5 元、合计里却没有——直接摘掉并说明原因。
watch(quoteTotalBeforeCoupon, (t) => {
  const c = chosenCoupon.value
  if (c && t && t + 0.001 < c.threshold) {
    couponId.value = undefined
    ElMessage.warning(`订单不足 ${c.threshold} 元，已取消使用「${c.couponName}」`)
  }
})

watch(
  () => [form.sender.lng, form.sender.lat, form.receiver.lng, form.receiver.lat,
    form.weightKg, form.deliveryType, form.goodsType, form.goodsName],
  () => { window.clearTimeout(timer); timer = window.setTimeout(doEstimate, 450) },
)

function validateForm(): string {
  for (const kind of ['sender', 'receiver'] as const) {
    const p = form[kind]
    if (!p.name) return `请填写${kind === 'sender' ? '寄' : '收'}件人姓名`
    if (!/^1\d{10}$/.test(p.phone)) return `请填写正确的${kind === 'sender' ? '寄' : '收'}件人手机号`
    if (!p.address) return `请填写${kind === 'sender' ? '寄' : '收'}件地址`
    if (!p.lng) return `请在地图上选择${kind === 'sender' ? '寄' : '收'}件点`
  }
  if (!form.weightKg || form.weightKg <= 0) return '重量必须大于 0'
  return ''
}

async function submitOrder() {
  const err = validateForm()
  if (err) { ElMessage.warning(err); return }
  submitting.value = true
  try {
    const payload: OrderPayload = {
      senderName: form.sender.name, senderPhone: form.sender.phone, senderAddress: form.sender.address,
      senderLng: form.sender.lng, senderLat: form.sender.lat,
      receiverName: form.receiver.name, receiverPhone: form.receiver.phone, receiverAddress: form.receiver.address,
      receiverLng: form.receiver.lng, receiverLat: form.receiver.lat,
      goodsType: form.goodsType, goodsName: form.goodsName || undefined,
      weightKg: form.weightKg, deliveryType: form.deliveryType,
      expectAt: form.expectAt, remark: form.remark || undefined,
      couponId: couponToSend(),
    }
    createdOrder.value = await createOrderApi(payload)
    cashierVisible.value = true
  } finally {
    submitting.value = false
  }
}

async function pay() {
  if (!createdOrder.value) return
  paying.value = true
  try {
    await payOrderApi(createdOrder.value.id, payMethod.value)
    ElMessage.success('支付成功（Mock）')
    cashierVisible.value = false
    router.replace(`/portal/orders/${createdOrder.value.id}`)
  } finally {
    paying.value = false
  }
}

function goDetailLater() {
  cashierVisible.value = false
  if (createdOrder.value) router.replace(`/portal/orders/${createdOrder.value.id}`)
}

onMounted(async () => {
  myCouponsApi(1).then((r) => { coupons.value = r.list }).catch(() => { coupons.value = [] })
  try {
    await loadAmap()
    if (mapRef.value) {
      mapCtrl = await createPickerMap(mapRef.value, (kind, p) => {
        const target = form[kind]
        target.lng = p.lng
        target.lat = p.lat
        if (p.address) target.address = p.address
      })
      mapCtrl.setActive(activeKind.value)
      // 开发调试钩子（生产构建不挂载）：便于无鼠标环境下模拟地图选点
      if (import.meta.env.DEV) (window as any).__tsPicker = mapCtrl
    }
  } catch {
    mapError.value = amapError.value
  }
})

onBeforeUnmount(() => mapCtrl?.destroy())
</script>

<style lang="scss" scoped>
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.card-head h3 { margin: 0; color: var(--ts-primary-dark); font-size: 16px; }
.map-box { width: 100%; height: 360px; border-radius: 6px; overflow: hidden; border: 1px solid var(--ts-border); }
.map-tip { font-size: 12px; color: var(--ts-text-secondary); margin: 8px 0 0; }
.manual-coord { display: flex; gap: 8px; margin-top: 10px; }
.group-title {
  font-weight: 700; color: var(--ts-primary-dark); border-left: 3px solid var(--ts-gold);
  padding-left: 8px; margin: 4px 0 16px;
}
.fee-panel { position: sticky; top: 84px; }
.fee-panel h3 { margin: 0 0 14px; color: var(--ts-primary-dark); }
.fee-row { display: flex; justify-content: space-between; font-size: 13px; color: var(--ts-text-regular); line-height: 2.1; }
.coupon-pick {
  display: flex; justify-content: space-between; align-items: center; gap: 10px;
  font-size: 13px; color: var(--ts-text-regular); margin: 6px 0 2px;
}
.off { color: var(--ts-success, #17b978); font-weight: 700; }
.total-val { display: flex; align-items: baseline; gap: 8px; }
.orig { font-size: 12px; color: var(--ts-text-secondary); font-weight: 400; }
.fee-total {
  display: flex; justify-content: space-between; border-top: 1px dashed var(--ts-border);
  margin-top: 10px; padding-top: 12px; font-weight: 700; color: var(--ts-text);
}
.num { color: var(--ts-danger); font-size: 20px; }
.merchant { font-size: 12px; color: var(--ts-text-secondary); margin: 8px 0 16px; }
.submit-btn { width: 100%; background: var(--ts-primary); border-color: var(--ts-primary); }
.submit-btn:hover { background: var(--ts-primary-light); border-color: var(--ts-primary-light); }
.fee-note { font-size: 11.5px; color: var(--ts-text-secondary); text-align: center; margin-top: 10px; line-height: 1.6; }
.cashier p { margin: 6px 0; }
.cashier .amount { font-size: 15px; }
</style>
