<template>
  <div>
    <!-- 欢迎横幅 -->
    <div class="hero ts-card">
      <div>
        <h2>你好，{{ userStore.userInfo?.realName || '客户' }}</h2>
        <p>无人机同城配送 · 下单后最快 30 分钟送达，全程可追踪</p>
        <el-button type="primary" size="large" @click="$router.push('/portal/order/create')">
          <el-icon style="margin-right:6px"><Promotion /></el-icon>立即下单
        </el-button>
      </div>
      <div class="hero-steps">
        <span>地图选点</span><i>→</i><span>费用试算</span><i>→</i>
        <span>在线支付</span><i>→</i><span>全程追踪</span>
      </div>
    </div>

    <!-- 在途订单 -->
    <div class="section-head">
      <h3>进行中的订单</h3>
      <el-button link type="primary" @click="$router.push('/portal/orders')">全部订单</el-button>
    </div>
    <div v-loading="loading">
      <div v-if="activeOrders.length === 0 && !loading" class="ts-card empty">
        暂无进行中的订单，<el-button link type="primary" @click="$router.push('/portal/order/create')">去下单</el-button>
      </div>
      <el-row v-else :gutter="16">
        <el-col v-for="o in activeOrders" :key="o.id" :span="8">
          <div class="ts-card order-card" @click="$router.push(`/portal/orders/${o.id}`)">
            <div class="oc-top">
              <span class="oc-no">{{ o.orderNo }}</span>
              <el-tag :type="ORDER_STATUS_MAP[o.status]?.type" size="small">{{ o.statusName }}</el-tag>
            </div>
            <div class="oc-route">
              <span class="dot send" />{{ o.senderAddress }}
              <br /><span class="dot recv" />{{ o.receiverAddress }}
            </div>
            <div class="oc-foot">
              <span>{{ o.goodsType }} · {{ o.weightKg }}kg</span>
              <span class="num">{{ yuan(o.totalAmount) }}</span>
            </div>
          </div>
        </el-col>
      </el-row>
    </div>

    <!-- 服务说明 -->
    <div class="section-head"><h3>服务说明</h3></div>
    <el-row :gutter="16">
      <el-col v-for="f in features" :key="f.t" :span="8">
        <div class="ts-card feature">
          <el-icon size="24" color="#23406E"><component :is="f.i" /></el-icon>
          <div class="t">{{ f.t }}</div>
          <div class="d">{{ f.d }}</div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Promotion } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { listOrdersApi, type OrderVO } from '@/api/order'
import { ORDER_STATUS_MAP, ACTIVE_STATUS, yuan } from '@/constants/dict'

const userStore = useUserStore()
const loading = ref(false)
const activeOrders = ref<OrderVO[]>([])

const features = [
  { t: '合规飞行', d: '航线自动避让禁飞区，飞行计划空管审批', i: 'CircleCheck' },
  { t: '实时追踪', d: '无人机位置、电量、进度全程可见', i: 'Position' },
  { t: '安全结算', d: '送达确认后结算，支持电子回单', i: 'Wallet' },
]

onMounted(async () => {
  loading.value = true
  try {
    // 拉最近 50 单，前端筛在途（W3 后端会支持状态集合查询）
    const res = await listOrdersApi({ page: 1, pageSize: 50 })
    activeOrders.value = res.list.filter((o) => ACTIVE_STATUS.includes(o.status)).slice(0, 3)
  } finally {
    loading.value = false
  }
})
</script>

<style lang="scss" scoped>
.hero {
  background: linear-gradient(120deg, #23406e 0%, #2c4d82 100%);
  color: #f3eedd; padding: 30px 32px; display: flex; justify-content: space-between;
  align-items: center; flex-wrap: wrap; gap: 16px;
  :deep(.el-button--primary) { background: var(--ts-gold); border-color: var(--ts-gold); color: #1c3358; }
  :deep(.el-button--primary:hover) { background: var(--ts-gold-light); border-color: var(--ts-gold-light); }
}
.hero h2 { margin: 0 0 8px; }
.hero p { margin: 0 0 18px; color: #c6d0e2; font-size: 13px; }
.hero-steps { color: var(--ts-gold-light); font-size: 13px; display: flex; gap: 8px; align-items: center; }
.hero-steps i { color: #8fa0bb; font-style: normal; }
.section-head { display: flex; justify-content: space-between; align-items: center; margin: 24px 0 12px; }
.section-head h3 { margin: 0; color: var(--ts-primary-dark); font-size: 16px; }
.empty { text-align: center; color: var(--ts-text-secondary); font-size: 13px; padding: 36px 0; }
.order-card { cursor: pointer; transition: box-shadow .2s; margin-bottom: 16px; }
.order-card:hover { box-shadow: 0 6px 18px rgba(28,51,88,.12); }
.oc-top { display: flex; justify-content: space-between; align-items: center; }
.oc-no { font-size: 12px; color: var(--ts-text-secondary); }
.oc-route { margin: 10px 0; font-size: 13px; line-height: 1.9; color: var(--ts-text); }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.dot.send { background: var(--ts-primary); }
.dot.recv { background: var(--ts-gold); }
.oc-foot { display: flex; justify-content: space-between; color: var(--ts-text-secondary); font-size: 12px; }
.oc-foot .num { color: var(--ts-danger); font-weight: 700; }
.feature { text-align: center; padding: 24px 16px; }
.t { font-weight: 600; margin: 10px 0 6px; color: var(--ts-text); }
.d { font-size: 12px; color: var(--ts-text-secondary); }
</style>
