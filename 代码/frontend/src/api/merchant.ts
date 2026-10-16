/** 商家入驻与审核（M-82）：门户自助申请 + 平台资质审核 */
import request from '@/utils/request'

export interface PricingConfig {
  basePerKm: number
  perKg: number
  urgentRate: number
}

export interface MerchantApplyForm {
  merchantName: string
  unifiedCreditCode: string
  legalPerson: string
  contactPhone: string
  serviceDistricts?: string[]
  pricing?: PricingConfig
  expireAt?: string | null
}

export interface MerchantVO {
  id: number
  merchantNo: string
  merchantName: string
  unifiedCreditCode: string
  legalPerson: string
  /** 审核侧是明文，申请人只拿到脱敏值 */
  contactPhone: string | null
  contactUserId: number | null
  serviceDistricts: string[]
  pricing: PricingConfig | null
  auditStatus: number
  auditStatusName: string
  auditOpinion: string | null
  auditedAt: string | null
  expireAt: string | null
  status: number
  createdAt: string | null
}

export interface MerchantQuery {
  page?: number
  pageSize?: number
  auditStatus?: number | ''
  keyword?: string
}

export const applyMerchantApi = (data: MerchantApplyForm) =>
  request.post<any, MerchantVO>('/system/merchants/apply', data)

/** 我提交过的最新一条申请，没有则 null */
export const myMerchantApplyApi = () =>
  request.get<any, MerchantVO | null>('/system/merchants/mine')

export const merchantsAuditListApi = (q: MerchantQuery) => {
  const params: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(q)) if (v !== '' && v !== undefined && v !== null) params[k] = v
  return request.get<any, { total: number; page: number; pageSize: number; list: MerchantVO[] }>(
    '/system/merchants', { params },
  )
}

/** result：1 通过（进承接池并转商家身份）2 驳回（必须给原因） */
export const auditMerchantApi = (mid: number, result: 1 | 2, opinion?: string) =>
  request.post<any, MerchantVO>(`/system/merchants/${mid}/audit`, { result, opinion })
