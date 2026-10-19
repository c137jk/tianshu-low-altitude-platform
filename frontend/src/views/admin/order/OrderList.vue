<template>
  <div>
    <div class="ts-card filter-bar">
      <el-radio-group v-model="q.status" size="default" @change="reload">
        <el-radio-button v-for="t in STAFF_ORDER_TABS" :key="String(t.value)" :value="t.value">
          {{ t.label }}
        </el-radio-button>
      </el-radio-group>
      <el-input
        v-model="q.keyword"
        placeholder="订单号 / 手机号 / 姓名 / 地址 / 物品"
        clearable
        style="width: 280px"
        @keyup.enter="reload"
        @clear="reload"
      >
        <template #append><el-button :icon="Search" @click="reload" /></template>
      </el-input>
      <el-button :icon="Refresh" @click="reload">刷新</el-button>
      <div class="spacer" />
      <el-tag v-if="options.scope === 'merchant'" type="info" effect="plain">仅本商家订单</el-tag>
      <el-button
        v-if="userStore.hasPerm('order:export')"
        :icon="Download"
        :loading="exporting"
        @click="exportCsv"
      >导出 CSV</el-button>
    </div>

    <div class="ts-card filter-bar" style="margin-top: 12px">
      <span class="lab">下单时间</span>
      <el-date-picker
        v-model="range"
        type="daterange"
        value-format="YYYY-MM-DD"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        style="width: 230px"
        @change="reload"
      />
      <el-select v-model="q.deliveryType" placeholder="配送类型" style="width: 112px" @change="reload">
        <el-option label="全部" value="" />
        <el-option label="普通" :value="1" />
        <el-option label="加急" :value="2" />
      </el-select>
      <el-select v-model="q.orderType" placeholder="订单类型" style="width: 124px" @change="reload">
        <el-option label="全部" value="" />
        <el-option label="普通配送" :value="1" />
        <el-option label="应急配送" :value="2" />
      </el-select>
      <el-select v-model="q.goodsType" placeholder="物品类型" style="width: 112px" clearable @change="reload">
        <el-option v-for="g in options.goodsTypes" :key="g" :label="g" :value="g" />
      </el-select>
      <el-select
        v-if="options.scope === 'platform'"
        v-model="q.merchantId"
        placeholder="承接商家"
        style="width: 180px"
        clearable
        @change="reload"
      >
        <el-option v-for="m in options.merchants" :key="m.id" :label="m.name" :value="m.id" />
      </el-select>
      <el-button link type="primary" @click="resetFilters">重置条件</el-button>
    </div>

    <div class="ts-card" style="margin-top: 12px">
      <el-table :data="list" v-loading="loading" stripe size="default">
        <el-table-column label="订单号" min-width="190">
          <template #default="{ row }">
            <router-link :to="`/admin/order/${row.id}`" class="num link">{{ row.orderNo }}</router-link>
          </template>
        </el-table-column>
        <el-table-column label="寄件人" min-width="150">
          <template #default="{ row }">
            {{ row.senderName }}<span class="num sub">{{ row.senderPhone }}</span>
          </template>
        </el-table-column>
        <el-table-column label="收件人" min-width="150">
          <template #default="{ row }">
            {{ row.receiverName }}<span class="num sub">{{ row.receiverPhone }}</span>
          </template>
        </el-table-column>
        <el-table-column label="物品 / 重量" min-width="120">
          <template #default="{ row }">
            {{ row.goodsType }}<span class="sub"> / {{ row.weightKg }}kg</span>
            <el-tag v-if="row.deliveryType === 2" size="small" type="danger" effect="plain">加急</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="费用" width="100" align="right">
          <template #default="{ row }"><span class="num">{{ yuan(row.payAmount || row.totalAmount) }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="ORDER_STATUS_MAP[row.status]?.type" effect="light">
              {{ row.statusName }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="下单时间" width="165">
          <template #default="{ row }"><span class="num sub">{{ row.createdAt }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 1" type="primary" size="small" @click="accept(row)">接单</el-button>
            <el-button v-if="row.status === 1" size="small" @click="reject(row)">拒单</el-button>
            <el-button
              v-if="row.status === 2 && userStore.hasPerm('dispatch:plan')"
              type="warning"
              size="small"
              @click="goBoard(row)"
            >去调度</el-button>
            <el-button
              v-if="canTerminate(row) "
              type="danger"
              size="small"
              plain
              @click="forceCancel(row)"
            >终止</el-button>
            <el-button size="small" link type="primary" @click="$router.push(`/admin/order/${row.id}`)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <p class="foot-note">
        终止用于订单已进入调度/审批但必须整单作废的场景（客户自助取消只能到「待接单」为止），
        已支付款项原路退回并通知客户；「飞行中」及之后的订单请走应急处置，不在此列。
      </p>

      <el-pagination
        class="pager"
        layout="total, sizes, prev, pager, next, jumper"
        :total="total"
        :page-size="q.pageSize"
        :current-page="q.page"
        @current-change="onPage"
        @size-change="onSize"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh, Download } from '@element-plus/icons-vue'
import {
  listOrdersApi, acceptOrderApi, rejectOrderApi, forceCancelOrderApi,
  exportOrdersApi, orderFilterOptionsApi, type OrderVO, type OrderQuery,
} from '@/api/order'
import { ORDER_STATUS_MAP, STAFF_ORDER_TABS, yuan } from '@/constants/dict'
import { useUserStore } from '@/stores/user'
import { today } from '@/utils/date'

const router = useRouter()
const userStore = useUserStore()
const list = ref<OrderVO[]>([])
const total = ref(0)
const loading = ref(false)
const exporting = ref(false)
const range = ref<[string, string] | null>(null)
const options = reactive<{ merchants: { id: number; name: string }[]; goodsTypes: string[]; scope: string }>({
  merchants: [], goodsTypes: [], scope: 'platform',
})

const q = reactive<OrderQuery & { page: number; pageSize: number }>({
  page: 1, pageSize: 10, status: 1, keyword: '', deliveryType: '', orderType: '',
  merchantId: '', goodsType: '',
})

/** 列表请求参数：日期区间单独拼（el-date-picker 给的是数组） */
function params(): OrderQuery & { page: number; pageSize: number } {
  return {
    ...q,
    start: range.value?.[0] || undefined,
    end: range.value?.[1] || undefined,
  }
}

async function reload() {
  loading.value = true
  try {
    const res = await listOrdersApi(params())
    list.value = res.list
    total.value = res.total
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  const res = await orderFilterOptionsApi()
  options.merchants = res.merchants
  options.goodsTypes = res.goodsTypes
  options.scope = res.scope
}

function onPage(p: number) {
  q.page = p
  reload()
}

function onSize(s: number) {
  q.pageSize = s
  q.page = 1
  reload()
}

function resetFilters() {
  Object.assign(q, {
    page: 1, status: '', keyword: '', deliveryType: '', orderType: '', merchantId: '', goodsType: '',
  })
  range.value = null
  reload()
}

async function exportCsv() {
  exporting.value = true
  try {
    const r = await exportOrdersApi(params(), `订单明细_${today()}.csv`)
    ElMessage.success(`已导出 ${r.count} 条` + (r.truncated ? '（已达 5000 行上限，请收窄筛选条件）' : ''))
  } finally {
    exporting.value = false
  }
}

/** 0~4 段都能终止（0 待支付留着也没人付，运营需要能关掉它）；5 飞行中往后走应急流程 */
const canTerminate = (row: OrderVO) =>
  userStore.hasPerm('order:force_cancel') && row.status >= 0 && row.status <= 4

async function accept(row: OrderVO) {
  await ElMessageBox.confirm(`确认承接订单 ${row.orderNo}？承接后进入待调度。`, '商家接单', {
    type: 'info', confirmButtonText: '确认接单', cancelButtonText: '取消',
  })
  await acceptOrderApi(row.id)
  ElMessage.success('已接单，订单进入待调度')
  reload()
}

async function reject(row: OrderVO) {
  const { value } = await ElMessageBox.prompt('请填写拒单原因（将原路退回客户已支付款项）', '商家拒单', {
    confirmButtonText: '确认拒单', cancelButtonText: '取消',
    inputPlaceholder: '如：超出当前配送范围 / 天气原因无法执飞',
    inputValidator: (v) => (v && v.trim() ? true : '拒单原因不能为空'),
  })
  await rejectOrderApi(row.id, value!)
  ElMessage.success('已拒单并发起退款（Mock）')
  reload()
}

async function forceCancel(row: OrderVO) {
  const { value } = await ElMessageBox.prompt(
    `终止订单 ${row.orderNo}（当前「${row.statusName}」）：原因会写入订单并通知客户` +
    (row.paid ? '，已支付金额原路退回（Mock）。' : '，该单未支付，无需退款。'),
    '强制终止订单',
    {
      confirmButtonText: '确认终止', cancelButtonText: '取消',
      inputPlaceholder: '如：重复下单 / 客户撤销 / 资质核验不通过',
      inputValidator: (v) => (v && v.trim() ? true : '终止原因不能为空'),
    },
  )
  await forceCancelOrderApi(row.id, value!)
  ElMessage.success('已强制终止，关联调度方案一并作废')
  reload()
}

function goBoard(row: OrderVO) {
  router.push({ path: '/admin/dispatch/board', query: { orderId: String(row.id) } })
}

onMounted(() => {
  loadOptions()
  reload()
})
</script>

<style lang="scss" scoped>
.filter-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.lab { color: var(--ts-text-secondary); font-size: 12.5px; }
.spacer { flex: 1; }
.sub { color: var(--ts-text-secondary); font-size: 12px; margin-left: 4px; }
.link { font-size: 12.5px; }
.pager { margin-top: 14px; justify-content: flex-end; }
.foot-note { margin: 10px 0 0; color: var(--ts-text-secondary); font-size: 12px; line-height: 1.7; }
</style>
