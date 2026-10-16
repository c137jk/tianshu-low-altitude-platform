/** W6 结算域接口（设计06 M-71~M-74） */
import request from '@/utils/request'

/** 账单状态：1待结算 2已结算 3对账中 4有差异 */
export const BILL_STATUS_MAP: Record<number, { label: string; type: 'warning' | 'success' | 'primary' | 'danger' }> = {
  1: { label: '待结算', type: 'warning' },
  2: { label: '已结算', type: 'success' },
  3: { label: '对账中', type: 'primary' },
  4: { label: '有差异', type: 'danger' },
}

export interface BillVO {
  id: number; settlementNo: string; orderId: number; orderNo: string | null; goodsName: string | null
  merchantId: number
  baseFee: number; airspaceFee: number; urgentFee: number; platformFee: number
  /** 账单金额 = 向客户收取的钱，应与该订单支付流水净额相等 */
  payAmount: number
  /** 派生值：账单金额 - 平台抽成 */
  merchantIncome: number
  status: number; statusName: string
  invoiceStatus: number; reconciledAt: string | null; createdAt: string | null
  /** 支付流水净额（成功支付 - 已退款），没有流水为 null */
  netPaid: number | null
  diffAmount: number | null
  diffReason: string | null
}

export interface BillSummary {
  count: number; totalFee: number; pendingFee: number; diffCount: number; diffFee: number
}

export interface BillQuery {
  status?: number; start?: string; end?: string; keyword?: string; onlyDiff?: boolean
}

export const listBillsApi = (q: BillQuery = {}) =>
  request<{ list: BillVO[]; total: number; summary: BillSummary; scope: 'merchant' | 'platform' }>(
    '/settlement/bills',
    { params: { ...q, onlyDiff: q.onlyDiff ? 1 : undefined } })

export interface ReconDiff {
  settlementNo: string; orderNo: string | null; payAmount: number
  netPaid: number | null; diffAmount: number; reason: string
}

export interface ReconcileResult {
  checked: number; matched: number; diffCount: number; diffs: ReconDiff[]
}

/** 对账：拿支付流水逐单核账单，平→已结算，不平→有差异。ids 不传按当前范围全量。 */
export const reconcileApi = (ids?: number[]) =>
  request<ReconcileResult>('/settlement/bills/reconcile', { method: 'POST', data: ids?.length ? { ids } : {} })

export interface PaymentVO {
  id: number; txnNo: string; orderNo: string | null; orderId: number | null; userId: number
  amount: number; direction: number; directionName: string
  status: number; statusName: string; payMethod: number; payMethodName: string
  channelTxnNo: string | null; paidAt: string | null; createdAt: string | null
}

export interface PaymentQuery {
  status?: number; direction?: number; start?: string; end?: string; keyword?: string
}

/** 管理端支付流水（M-73）：客户侧那个 /bills/payments 只查本人，财务核差异必须看全量。 */
export const listPayFlowsApi = (q: PaymentQuery = {}) =>
  request<{
    list: PaymentVO[]; total: number; scope: 'merchant' | 'platform'
    summary: { income: number; refund: number; net: number }
  }>('/settlement/payments', { params: q })

export const applyInvoiceApi = (id: number, body: { title?: string; taxNo?: string } = {}) =>
  request<{ id: number; invoiceStatus: number; invoiceTitle: string | null }>(
    `/settlement/bills/${id}/invoice`, { method: 'POST', data: body })

/** 开具发票（settlement:reconcile，商家侧只能申请） */
export const issueInvoiceApi = (id: number) =>
  request<{ id: number; invoiceStatus: number; invoiceTitle: string | null }>(
    `/settlement/bills/${id}/invoice/issue`, { method: 'POST', data: {} })
