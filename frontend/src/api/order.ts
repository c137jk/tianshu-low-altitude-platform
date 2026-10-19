/** D2 订单领域接口（对应后端 /api/orders/*、/api/bills/*） */
import request from '@/utils/request'
import { downloadCsv } from '@/utils/download'

export interface OrderLocationPayload {
  name: string
  phone: string
  address: string
  lng: number
  lat: number
}

export interface OrderPayload {
  senderName: string
  senderPhone: string
  senderAddress: string
  senderLng: number
  senderLat: number
  receiverName: string
  receiverPhone: string
  receiverAddress: string
  receiverLng: number
  receiverLat: number
  goodsType: string
  goodsName?: string
  weightKg: number
  volumeM3?: number | null
  quantity?: number
  deliveryType: 1 | 2
  expectAt?: string | null
  remark?: string
  /** 「我的券包」里那张券的 id（user_coupon.id），不是券模板 id */
  couponId?: number | null
}

export interface EstimateResult {
  merchantId: number
  merchantName: string
  distanceM: number
  estDurationSec: number
  crossLimitedZones: string[]
  baseFee: number
  airspaceFee: number
  urgentFee: number
  discountAmount: number
  totalAmount: number
  /** 带 couponId 试算时后端回来的抵扣凭据，不带则为 undefined */
  coupon?: { id: number; couponId: number; name: string; rule: string; discount: number } | null
}

export interface OrderVO extends OrderPayload {
  id: number
  orderNo: string
  customerId: number
  merchantId: number | null
  orderType: number
  status: number
  statusName: string
  baseFee: number
  airspaceFee: number
  urgentFee: number
  discountAmount: number
  totalAmount: number
  payAmount: number
  /** 用券下单时回显券名（后端只在 discountAmount>0 时查一次） */
  couponName?: string | null
  payMethod: number | null
  payMethodName: string | null
  paid: boolean
  paidAt: string | null
  deliveredAt: string | null
  finishedAt: string | null
  deliveryMinutes: number | null
  cancelReason: string | null
  createdAt: string | null
}

export interface PageResult<T> {
  list: T[]
  total: number
  page: number
  pageSize: number
}

export interface PaymentVO {
  id: number
  txnNo: string
  orderId: number
  orderNo: string
  amount: number
  direction: number
  directionName: string
  payMethod: number
  payMethodName: string
  status: number
  statusName: string
  channelTxnNo: string | null
  paidAt: string | null
  createdAt: string
}

export interface SettlementVO {
  id: number
  settlementNo: string
  orderId: number
  orderNo: string
  baseFee: number
  airspaceFee: number
  urgentFee: number
  platformFee: number
  payAmount: number
  status: number
  statusName: string
  invoiceStatus: number
  createdAt: string
}

export const estimateApi = (data: Partial<OrderPayload>) =>
  request.post<any, EstimateResult>('/orders/estimate', data)

export const createOrderApi = (data: OrderPayload) =>
  request.post<any, OrderVO>('/orders', data)

/**
 * 列表 / 导出共用同一套筛选条件（后端 _filters 一份代码）。
 * keyword 命中：订单号、物品类型/名称、寄收件人姓名、地址、手机号（整号或后 4 位）。
 * 手机号能搜是因为 orders 表存了 HMAC 摘要列——密文本身不可 LIKE。
 */
export interface OrderQuery {
  page?: number
  pageSize?: number
  status?: number | ''
  keyword?: string
  /** 下单时间区间，YYYY-MM-DD 或到时分秒；右端只给日期时后端自动补到当天结束 */
  start?: string
  end?: string
  deliveryType?: number | ''
  orderType?: number | ''
  merchantId?: number | ''
  paid?: number | ''
  goodsType?: string
}

/** 空串表示"不限"，后端按 str(value) == '' 跳过条件 */
function cleanQuery(q: OrderQuery): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(q)) if (v !== '' && v !== undefined && v !== null) out[k] = v
  return out
}

export const listOrdersApi = (params: OrderQuery) =>
  request.get<any, PageResult<OrderVO>>('/orders', { params: cleanQuery(params) })

/** 筛选下拉的数据源：商家/物品类型只给出当前账号可见范围内的取值 */
export interface OrderFilterOptions {
  merchants: { id: number; name: string }[]
  goodsTypes: string[]
  scope: 'merchant' | 'platform'
}

export const orderFilterOptionsApi = () =>
  request.get<any, OrderFilterOptions>('/orders/filter-options')

/** 管理端 CSV 导出（order:export）：与列表同样的筛选条件 */
export const exportOrdersApi = (q: OrderQuery, filename: string) =>
  downloadCsv('/orders/export', cleanQuery({ ...q, page: undefined, pageSize: undefined }), filename)

export interface BatchRow {
  line: number
  key: string
  ok: boolean
  reason: string | null
  orderId?: number
  orderNo?: string
  totalAmount?: number
  distanceM?: number
}

export interface BatchResult {
  total: number
  succeeded: number
  failed: number
  validateOnly: boolean
  maxRows: number
  rows: BatchRow[]
}

/** M-23 批量导入下单：dryRun=true 只校验不落库 */
export const batchImportOrdersApi = (file: File, dryRun: boolean) => {
  const form = new FormData()
  form.append('file', file)
  if (dryRun) form.append('dryRun', '1')
  return request.post<any, BatchResult>('/orders/batch', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
}

export const batchTemplateDownloadApi = () =>
  downloadCsv('/orders/batch/template', {}, '批量导入下单模板.csv')

/** 需求 2.2.2-3-1 强制终止（order:force_cancel）：0~4 段唯一出口，已支付自动退款 */
export const forceCancelOrderApi = (id: number, reason: string) =>
  request.post<any, null>(`/orders/${id}/force-cancel`, { reason })

export const getOrderApi = (id: number | string) =>
  request.get<any, OrderVO>(`/orders/${id}`)

export const payOrderApi = (id: number, payMethod: number) =>
  request.post<any, null>(`/orders/${id}/pay`, { payMethod })

export const cancelOrderApi = (id: number, reason: string) =>
  request.post<any, null>(`/orders/${id}/cancel`, { reason })

export const confirmOrderApi = (id: number) =>
  request.post<any, null>(`/orders/${id}/confirm`)

export const acceptOrderApi = (id: number) =>
  request.post<any, null>(`/orders/${id}/accept`)

export const rejectOrderApi = (id: number, reason: string) =>
  request.post<any, null>(`/orders/${id}/reject`, { reason })

export const listPaymentsApi = (params: { page: number; pageSize: number }) =>
  request.get<any, PageResult<PaymentVO>>('/bills/payments', { params })

export const listSettlementsApi = (params: { page: number; pageSize: number }) =>
  request.get<any, PageResult<SettlementVO>>('/bills/settlements', { params })
