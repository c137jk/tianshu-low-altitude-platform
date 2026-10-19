<template>
  <div>
    <el-row :gutter="12" style="margin-bottom: 12px">
      <el-col :span="6">
        <el-card shadow="hover"><div class="k">流水笔数（本列表）</div><div class="v">{{ list.length }} 笔</div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover"><div class="k">支付成功收入</div><div class="v good">{{ yuan(summary.income) }}</div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover"><div class="k">已退款</div><div class="v bad">{{ yuan(summary.refund) }}</div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover"><div class="k">净收入（本列表）</div><div class="v">{{ yuan(summary.net) }}</div></el-card>
      </el-col>
    </el-row>

    <div class="ts-card filter-bar">
      <el-select v-model="q.direction" placeholder="方向" clearable style="width: 110px" @change="reload">
        <el-option label="支付" :value="1" />
        <el-option label="退款" :value="2" />
      </el-select>
      <el-select v-model="q.status" placeholder="状态" clearable style="width: 120px" @change="reload">
        <el-option v-for="(label, k) in PAY_STATUS" :key="k" :label="label" :value="Number(k)" />
      </el-select>
      <el-date-picker
        v-model="dates" type="daterange" value-format="YYYY-MM-DD" unlink-panels
        start-placeholder="交易起" end-placeholder="交易止" style="width: 240px" @change="reload"
      />
      <el-input
        v-model="q.keyword" placeholder="平台流水号 / 渠道流水号 / 订单号" clearable
        style="width: 260px" @keyup.enter="reload" @clear="reload"
      >
        <template #append><el-button :icon="Search" @click="reload" /></template>
      </el-input>
      <el-button :icon="Refresh" @click="reload">刷新</el-button>
      <span class="flex-gap" />
      <el-tag size="small" :type="scope === 'merchant' ? 'warning' : 'info'" effect="plain">
        {{ scope === 'merchant' ? '本商家订单流水' : '全平台流水' }}
      </el-tag>
      <el-button size="small" @click="router.push('/admin/settlement/bills')">返回账单</el-button>
    </div>

    <el-card v-loading="loading" style="margin-top: 12px">
      <el-table :data="list" stripe size="default">
        <el-table-column prop="txnNo" label="平台流水号" width="185" />
        <el-table-column prop="channelTxnNo" label="渠道流水号" width="185">
          <template #default="{ row }"><span class="sub num">{{ row.channelTxnNo || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="订单号" min-width="175">
          <template #default="{ row }">
            <!-- 订单详情页挂 order:list，财务角色没有这个权限点，硬链过去就是 404 -->
            <router-link v-if="row.orderId && canOrder" :to="`/admin/order/${row.orderId}`" class="num link">
              {{ row.orderNo }}
            </router-link>
            <span v-else :class="row.orderId ? 'num link' : 'sub'">{{ row.orderNo || '无关联订单' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="方向" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.direction === 1 ? 'success' : 'danger'" effect="plain">
              {{ row.directionName }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="110" align="right">
          <template #default="{ row }">
            <span class="num" :class="row.direction === 1 ? 'good' : 'bad'">
              {{ row.direction === 1 ? '+' : '-' }}{{ yuan(row.amount) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="payMethodName" label="支付方式" width="100" />
        <el-table-column label="状态" width="95">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ row.statusName }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="成交时间" width="150">
          <template #default="{ row }"><span class="sub">{{ row.paidAt || '—' }}</span></template>
        </el-table-column>
        <el-table-column prop="createdAt" label="记账时间" width="150" />
      </el-table>
      <div class="sub" style="margin-top: 8px">
        流水是 Mock 渠道回执（支付/退款都留三方流水号）。账单对账只认两种终态：
        「支付」+「支付成功」计入收入，「退款」+「已退款」从净额里扣；待支付/失败不计到账。
        汇总按当前筛选结果统计，最多列 200 笔。
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Search, Refresh } from '@element-plus/icons-vue'
import { listPayFlowsApi, type PaymentQuery, type PaymentVO } from '@/api/settlement'
import { yuan } from '@/constants/dict'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()
const canOrder = computed(() => userStore.hasPerm('order:list'))
const PAY_STATUS: Record<number, string> = { 1: '待支付', 2: '支付成功', 3: '支付失败', 4: '已退款' }
const list = ref<PaymentVO[]>([])
const summary = ref({ income: 0, refund: 0, net: 0 })
const scope = ref<'merchant' | 'platform'>('platform')
const loading = ref(true)
const dates = ref<[string, string] | null>(null)
const q = ref<PaymentQuery>({ direction: undefined, status: undefined, keyword: '' })

function statusType(st: number) {
  return st === 2 ? 'success' : st === 4 ? 'warning' : st === 3 ? 'danger' : 'info'
}

async function reload() {
  loading.value = true
  try {
    const r = await listPayFlowsApi({
      ...q.value,
      start: dates.value?.[0] || undefined,
      end: dates.value?.[1] || undefined,
      keyword: q.value.keyword || undefined,
    })
    list.value = r.list
    summary.value = r.summary
    scope.value = r.scope
  } finally {
    loading.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.k { font-size: 12px; color: #667; }
.v { font-size: 22px; font-weight: 700; color: var(--ts-primary-dark); margin-top: 4px; }
.filter-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.flex-gap { flex: 1; }
.sub { font-size: 12px; color: var(--ts-text-secondary); }
.num { font-family: Consolas, monospace; }
.link { font-size: 12.5px; }
.good { color: #18794e; }
.bad { color: #d33; }
</style>
