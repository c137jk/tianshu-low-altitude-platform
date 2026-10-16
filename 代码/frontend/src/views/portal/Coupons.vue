<template>
  <div class="ts-card">
    <el-tabs v-model="tab">
      <!-- 领券中心 -->
      <el-tab-pane label="领券中心" name="pool">
        <div v-loading="poolLoading" class="grid">
          <div v-for="c in pool" :key="c.id" class="coupon" :class="{ owned: c.claimed }">
            <div class="left">
              <div class="value">
                <template v-if="c.coupon_type === 2">{{ (Number(c.discount) * 10).toFixed(1) }}<em>折</em></template>
                <template v-else><em>¥</em>{{ Number(c.amount || 0).toFixed(2) }}</template>
              </div>
              <div class="rule">{{ Number(c.threshold) > 0 ? `满 ${Number(c.threshold)} 元可用` : '不限金额' }}</div>
            </div>
            <div class="right">
              <b class="name">{{ c.coupon_name }}</b>
              <p class="meta">有效期 {{ c.start_at }} 至 {{ c.end_at }}</p>
              <p class="meta">剩余 {{ c.remain }} / {{ c.total_count }} 张</p>
              <el-button
                size="small" :type="c.claimed ? 'info' : 'primary'" :disabled="c.claimed"
                :loading="claiming === c.id" @click="claim(c)"
              >{{ c.claimed ? '已领取' : '立即领取' }}</el-button>
            </div>
          </div>
          <el-empty v-if="!poolLoading && !pool.length" description="当前没有可领取的优惠券" />
        </div>
      </el-tab-pane>

      <!-- 我的券包 -->
      <el-tab-pane label="我的券包" name="mine">
        <div class="filter">
          <el-radio-group v-model="mineStatus" size="small" @change="loadMine">
            <el-radio-button :label="0">全部</el-radio-button>
            <el-radio-button :label="1">未使用</el-radio-button>
            <el-radio-button :label="2">已核销</el-radio-button>
          </el-radio-group>
        </div>
        <div v-loading="mineLoading" class="grid">
          <div v-for="m in mine" :key="m.id" class="coupon" :class="{ dead: m.status !== 1 }">
            <div class="left">
              <div class="value">
                <template v-if="m.couponType === 2">{{ (Number(m.discount) * 10).toFixed(1) }}<em>折</em></template>
                <template v-else><em>¥</em>{{ Number(m.amount || 0).toFixed(2) }}</template>
              </div>
              <div class="rule">{{ m.ruleText.split(' · ')[0] }}</div>
            </div>
            <div class="right">
              <b class="name">{{ m.couponName }}</b>
              <p class="meta">有效期至 {{ (m.endAt || '').slice(0, 10) }}</p>
              <p class="meta">领取于 {{ (m.gotAt || '').slice(0, 16) }}</p>
              <p v-if="m.status === 2" class="meta">核销订单 {{ m.orderNo }}</p>
              <div class="acts">
                <el-tag size="small" :type="TAG_TYPE[m.status] || 'info'">{{ m.statusName }}</el-tag>
                <el-button v-if="m.status === 1" size="small" type="primary" @click="toOrder">去使用</el-button>
              </div>
            </div>
          </div>
          <el-empty v-if="!mineLoading && !mine.length" description="券包还是空的，去「领券中心」领一张吧" />
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  availableCouponsApi, claimCouponApi, myCouponsApi,
  type CouponTemplate, type MyCoupon,
} from '@/api/marketing'

const router = useRouter()
const tab = ref('pool')
const pool = ref<CouponTemplate[]>([])
const mine = ref<MyCoupon[]>([])
const poolLoading = ref(true)
const mineLoading = ref(false)
// 0 只在前端表示"全部"：后端 status 只认 1/2，不传就是全量
const mineStatus = ref<0 | 1 | 2>(0)
const claiming = ref<number | null>(null)

const TAG_TYPE: Record<number, string> = { 1: 'success', 2: 'info', 4: 'warning', 5: 'danger' }

async function loadPool() {
  poolLoading.value = true
  try { pool.value = await availableCouponsApi() } finally { poolLoading.value = false }
}

// 领券页一进来就把券包拉上：领完切过去不能是空的（"领了却没到账"最容易被当成 bug）
async function loadMine() {
  mineLoading.value = true
  try { mine.value = (await myCouponsApi(mineStatus.value || undefined)).list } finally { mineLoading.value = false }
}

async function claim(c: CouponTemplate) {
  claiming.value = c.id
  try {
    await claimCouponApi(c.id)
    ElMessage.success(`已领取「${c.coupon_name}」`)
    await Promise.all([loadPool(), loadMine()])
  } finally {
    claiming.value = null
  }
}

function toOrder() {
  router.push('/portal/order/create')
}

onMounted(() => { loadPool(); loadMine() })
</script>

<style lang="scss" scoped>
.filter { margin-bottom: 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; min-height: 120px; }
.coupon {
  display: flex; border: 1px solid var(--ts-border); border-radius: 8px; overflow: hidden;
  background: linear-gradient(135deg, #fff, #f7fbff);
}
.coupon.owned, .coupon.dead { background: #fafafa; filter: grayscale(0.5); }
.left {
  width: 118px; padding: 14px 10px; text-align: center; color: #fff;
  background: var(--ts-primary); display: flex; flex-direction: column; justify-content: center;
}
.value { font-size: 26px; font-weight: 700; line-height: 1.2; }
.value em { font-size: 14px; font-style: normal; margin: 0 2px; }
.rule { font-size: 11.5px; opacity: 0.85; margin-top: 6px; }
.right { flex: 1; padding: 12px 14px; }
.name { color: var(--ts-primary-dark); font-size: 15px; }
.meta { font-size: 12px; color: var(--ts-text-secondary); margin: 5px 0; }
.acts { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
</style>
