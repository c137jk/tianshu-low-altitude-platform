<template>
  <div>
    <h2 class="title">我的账单</h2>
    <el-tabs v-model="tab" @tab-change="load">
      <el-tab-pane label="支付/退款流水" name="payments" />
      <el-tab-pane label="结算单" name="settlements" />
    </el-tabs>

    <!-- 支付流水 -->
    <el-table v-if="tab === 'payments'" v-loading="loading" :data="payments" class="ts-card">
      <el-table-column label="流水号" prop="txnNo" width="190" />
      <el-table-column label="订单号" prop="orderNo" width="190">
        <template #default="{ row }">
          <el-button link type="primary" @click="$router.push(`/portal/orders/${row.orderId}`)">{{ row.orderNo }}</el-button>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="90">
        <template #default="{ row }">
          <el-tag :type="row.direction === 1 ? 'warning' : 'info'" size="small">{{ row.directionName }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="金额" width="110">
        <template #default="{ row }">
          <span :class="row.direction === 1 ? 'num pay' : 'num refund'">
            {{ row.direction === 1 ? '-' : '+' }}{{ yuan(row.amount) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="支付方式" prop="payMethodName" width="110" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 2 ? 'success' : row.status === 4 ? 'info' : 'danger'" size="small">
            {{ row.statusName }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="渠道流水号" prop="channelTxnNo" min-width="160" />
      <el-table-column label="时间" prop="paidAt" width="170">
        <template #default="{ row }">{{ row.paidAt || row.createdAt }}</template>
      </el-table-column>
      <template #empty>暂无流水</template>
    </el-table>

    <!-- 结算单 -->
    <el-table v-else v-loading="loading" :data="settlements" class="ts-card">
      <el-table-column label="结算单号" prop="settlementNo" width="200" />
      <el-table-column label="订单号" prop="orderNo" width="200">
        <template #default="{ row }">
          <el-button link type="primary" @click="$router.push(`/portal/orders/${row.orderId}`)">{{ row.orderNo }}</el-button>
        </template>
      </el-table-column>
      <el-table-column label="基础运费" width="110">
        <template #default="{ row }">{{ yuan(row.baseFee) }}</template>
      </el-table-column>
      <el-table-column label="空域费" width="100">
        <template #default="{ row }">{{ yuan(row.airspaceFee) }}</template>
      </el-table-column>
      <el-table-column label="加急费" width="100">
        <template #default="{ row }">{{ yuan(row.urgentFee) }}</template>
      </el-table-column>
      <el-table-column label="实付金额" width="120">
        <template #default="{ row }"><span class="num pay">{{ yuan(row.payAmount) }}</span></template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 2 ? 'success' : 'warning'" size="small">{{ row.statusName }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="生成时间" prop="createdAt" width="170" />
      <template #empty>订单确认收货后生成结算单</template>
    </el-table>

    <el-pagination
      class="pager" background layout="prev, pager, next, total"
      :total="total" :page-size="pageSize" v-model:current-page="page" @current-change="load"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  listPaymentsApi, listSettlementsApi,
  type PaymentVO, type SettlementVO,
} from '@/api/order'
import { yuan } from '@/constants/dict'

const tab = ref('payments')
const loading = ref(false)
const page = ref(1)
const pageSize = 10
const total = ref(0)
const payments = ref<PaymentVO[]>([])
const settlements = ref<SettlementVO[]>([])

async function load() {
  loading.value = true
  try {
    if (tab.value === 'payments') {
      const res = await listPaymentsApi({ page: page.value, pageSize })
      payments.value = res.list
      total.value = res.total
    } else {
      const res = await listSettlementsApi({ page: page.value, pageSize })
      settlements.value = res.list
      total.value = res.total
    }
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style lang="scss" scoped>
.title { margin: 0 0 8px; color: var(--ts-primary-dark); }
.num { font-weight: 700; }
.num.pay { color: var(--ts-danger); }
.num.refund { color: var(--ts-success); }
.pager { margin-top: 16px; justify-content: flex-end; }
</style>
