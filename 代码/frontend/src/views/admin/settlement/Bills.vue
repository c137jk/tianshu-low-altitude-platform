<template>
  <div>
    <el-row :gutter="12" style="margin-bottom: 12px">
      <el-col :span="6">
        <el-card shadow="hover"><div class="k">账单笔数</div><div class="v">{{ summary.count }} 笔</div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="k">累计应收（客户实付口径）</div>
          <div class="v">{{ yuan(summary.totalFee) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="k">待结算金额</div>
          <div class="v">{{ yuan(summary.pendingFee) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" :class="{ warn: summary.diffCount > 0 }">
          <div class="k">对账差异</div>
          <div class="v">{{ summary.diffCount }} 笔
            <span class="sub">{{ yuan(summary.diffFee) }} 账单待核</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <div class="ts-card filter-bar">
      <el-select v-model="q.status" placeholder="账单状态" clearable style="width: 130px" @change="reload">
        <el-option v-for="(v, k) in BILL_STATUS_MAP" :key="k" :label="v.label" :value="Number(k)" />
      </el-select>
      <el-date-picker
        v-model="dates" type="daterange" value-format="YYYY-MM-DD" unlink-panels
        start-placeholder="出账起" end-placeholder="出账止" style="width: 240px" @change="reload"
      />
      <el-input
        v-model="q.keyword" placeholder="结算单号 / 订单号 / 货物" clearable
        style="width: 230px" @keyup.enter="reload" @clear="reload"
      >
        <template #append><el-button :icon="Search" @click="reload" /></template>
      </el-input>
      <el-checkbox v-model="q.onlyDiff" @change="reload">只看差异账单</el-checkbox>
      <el-button :icon="Refresh" @click="reload">刷新</el-button>
      <span class="flex-gap" />
      <el-tag size="small" :type="scope === 'merchant' ? 'warning' : 'info'" effect="plain">
        {{ scope === 'merchant' ? '本商家账单' : '全平台账单' }}
      </el-tag>
      <el-button size="small" @click="router.push('/admin/settlement/payments')">支付流水</el-button>
      <el-button
        v-if="userStore.hasPerm('settlement:reconcile')" type="primary" :loading="reconcing"
        :disabled="!reconable" @click="doReconcile"
      >
        一键对账{{ reconable ? `（${reconable} 笔）` : '' }}
      </el-button>
    </div>

    <el-alert v-if="recon" class="recon" type="info" :closable="true" show-icon @close="recon = null">
      <template #title>
        本次核对 {{ recon.checked }} 笔：{{ recon.matched }} 笔流水与账单相符已结算，
        {{ recon.diffCount }} 笔有差异
      </template>
      <el-table v-if="recon.diffs.length" :data="recon.diffs" size="small" :show-header="true">
        <el-table-column label="结算单号" prop="settlementNo" width="180" />
        <el-table-column label="订单号" prop="orderNo" width="180" />
        <el-table-column label="账单金额" width="100" align="right">
          <template #default="{ row }"><span class="num">{{ yuan(row.payAmount) }}</span></template>
        </el-table-column>
        <el-table-column label="流水净额" width="100" align="right">
          <template #default="{ row }"><span class="num">{{ yuan(row.netPaid ?? 0) }}</span></template>
        </el-table-column>
        <el-table-column label="差额" width="100" align="right">
          <template #default="{ row }"><span class="num bad">{{ yuan(row.diffAmount) }}</span></template>
        </el-table-column>
        <el-table-column label="差异原因" prop="reason" min-width="260" />
      </el-table>
      <div v-else class="sub">全部平账，没有需要人工处理的差异。</div>
    </el-alert>

    <el-card v-loading="loading">
      <el-table :data="list" stripe size="default" :row-class-name="rowClass">
        <el-table-column prop="settlementNo" label="结算单号" width="170" />
        <el-table-column prop="orderNo" label="订单号" width="170" />
        <el-table-column prop="goodsName" label="货物" min-width="120" />
        <el-table-column label="账单金额" width="105" align="right">
          <template #default="{ row }"><b class="num">{{ yuan(row.payAmount) }}</b></template>
        </el-table-column>
        <el-table-column label="平台抽成" width="95" align="right">
          <template #default="{ row }"><span class="num sub">{{ yuan(row.platformFee) }}</span></template>
        </el-table-column>
        <el-table-column label="商家实收" width="100" align="right">
          <template #default="{ row }"><span class="num">{{ yuan(row.merchantIncome) }}</span></template>
        </el-table-column>
        <el-table-column label="流水净额" width="100" align="right">
          <template #default="{ row }">
            <span class="num" :class="{ bad: row.diffReason }">{{ yuan(row.netPaid ?? 0) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="对账结果" min-width="180">
          <template #default="{ row }">
            <span v-if="row.diffReason" class="bad">{{ row.diffReason }}</span>
            <span v-else-if="row.status === 2" class="good">流水相符</span>
            <span v-else class="sub">未对账</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="95">
          <template #default="{ row }">
            <el-tag size="small" :type="BILL_STATUS_MAP[row.status]?.type || 'info'">{{ row.statusName }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="发票" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.invoiceStatus === 2" size="small" type="success">已开具</el-tag>
            <template v-else-if="row.invoiceStatus === 1">
              <el-tag size="small" type="warning">已申请</el-tag>
              <el-button
                v-if="userStore.hasPerm('settlement:reconcile')"
                size="small" link type="primary" @click="issue(row)"
              >开具</el-button>
            </template>
            <el-button
              v-else-if="row.status === 2" size="small" link type="primary" @click="openApply(row)"
            >申请发票</el-button>
            <span v-else class="sub">对账平账后可开票</span>
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="出账时间" width="140" />
        <el-table-column prop="reconciledAt" label="对账时间" width="140">
          <template #default="{ row }"><span class="sub">{{ row.reconciledAt || '—' }}</span></template>
        </el-table-column>
      </el-table>
      <div class="sub" style="margin-top: 8px">
        账单金额按"向客户收取的钱"记账，与订单支付流水一一对应；商家实收 = 账单金额 − 平台抽成（派生列）。
        对账只做"账单 vs 流水"核对，平账才置为已结算，不平的挂差异原因等财务处理（补退款单或冲减账单）后重核。
      </div>
    </el-card>

    <el-dialog v-model="dlg" title="申请发票" width="440px">
      <el-descriptions :column="1" border size="small" style="margin-bottom: 12px">
        <el-descriptions-item label="结算单号">{{ cur.settlementNo }}</el-descriptions-item>
        <el-descriptions-item label="订单号">{{ cur.orderNo }}</el-descriptions-item>
        <el-descriptions-item label="开票金额">{{ yuan(cur.payAmount ?? 0) }}</el-descriptions-item>
      </el-descriptions>
      <el-form label-width="86px">
        <el-form-item label="发票抬头">
          <el-input v-model="form.title" placeholder="留空则默认使用商户名称作为抬头" />
        </el-form-item>
        <el-form-item label="纳税人识别号">
          <el-input v-model="form.taxNo" placeholder="选填，开具时也可补" />
        </el-form-item>
      </el-form>
      <div class="sub">申请后由平台财务开具，状态：未申请 → 已申请 → 已开具。</div>
      <template #footer>
        <el-button size="small" @click="dlg = false">取消</el-button>
        <el-button size="small" type="primary" :loading="submitting" @click="confirmApply">提交申请</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh } from '@element-plus/icons-vue'
import {
  BILL_STATUS_MAP, listBillsApi, reconcileApi, applyInvoiceApi, issueInvoiceApi,
  type BillVO, type BillQuery, type ReconcileResult,
} from '@/api/settlement'
import { yuan } from '@/constants/dict'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()
const list = ref<BillVO[]>([])
const summary = ref({ count: 0, totalFee: 0, pendingFee: 0, diffCount: 0, diffFee: 0 })
const scope = ref<'merchant' | 'platform'>('platform')
const loading = ref(true)
const reconcing = ref(false)
const recon = ref<ReconcileResult | null>(null)
const dates = ref<[string, string] | null>(null)
const q = ref<BillQuery>({ status: undefined, keyword: '', onlyDiff: false })

/** 待结算 / 对账中 / 有差异都能进对账：差异单在财务补完流水后必须能重核 */
const reconable = computed(() => list.value.filter((b) => [1, 3, 4].includes(b.status)).length)

function rowClass({ row }: { row: BillVO }) {
  return row.diffReason ? 'row-diff' : ''
}

async function reload() {
  loading.value = true
  try {
    const r = await listBillsApi({
      ...q.value,
      start: dates.value?.[0] || undefined,
      end: dates.value?.[1] || undefined,
      keyword: q.value.keyword || undefined,
      status: q.value.status ?? undefined,
    })
    list.value = r.list
    summary.value = r.summary
    scope.value = r.scope
  } finally {
    loading.value = false
  }
}

async function doReconcile() {
  await ElMessageBox.confirm(
    '将按支付流水逐笔核对当前范围内待结算/对账中/有差异的账单：流水相符的置为已结算，金额不符的挂成差异单。继续？',
    '一键对账', { type: 'warning', confirmButtonText: '开始对账', cancelButtonText: '取消' })
  reconcing.value = true
  try {
    recon.value = await reconcileApi()
    ElMessage.success(`对账完成：${recon.value.matched} 笔平账，${recon.value.diffCount} 笔有差异`)
    await reload()
  } finally {
    reconcing.value = false
  }
}

const cur = ref<any>({})
const dlg = ref(false)
const submitting = ref(false)
const form = ref({ title: '', taxNo: '' })
function openApply(row: BillVO) {
  cur.value = row
  form.value = { title: '', taxNo: '' }
  dlg.value = true
}
async function confirmApply() {
  submitting.value = true
  try {
    await applyInvoiceApi(cur.value.id, { title: form.value.title || undefined, taxNo: form.value.taxNo || undefined })
    const row = list.value.find((r) => r.id === cur.value.id)
    if (row) row.invoiceStatus = 1
    dlg.value = false
    ElMessage.success('发票申请已提交，等待平台财务开具')
  } finally {
    submitting.value = false
  }
}

async function issue(row: BillVO) {
  await ElMessageBox.confirm(`确认已为 ${row.settlementNo} 开具发票？开票金额为账单金额 ${yuan(row.payAmount)}。`,
    '开具发票', { type: 'info', confirmButtonText: '确认开具', cancelButtonText: '取消' })
  await issueInvoiceApi(row.id)
  row.invoiceStatus = 2
  ElMessage.success('发票已开具')
}

onMounted(reload)
</script>

<style scoped>
.k { font-size: 12px; color: #667; }
.v { font-size: 22px; font-weight: 700; color: var(--ts-primary-dark); margin-top: 4px; }
.warn .v { color: #d33; }
.filter-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.flex-gap { flex: 1; }
.recon { margin-bottom: 12px; }
.sub { font-size: 12px; color: var(--ts-text-secondary); }
.num { font-family: Consolas, monospace; }
.bad { color: #d33; }
.good { color: #18794e; }
:deep(.row-diff) { background: #fff4f4; }
</style>
