/** 营销域接口：优惠券（后端 /api/aux/coupons*）
 *
 * aux 蓝图是裸 SQL，出参键名沿用库表 snake_case，只有后加的派生字段（remain/claimed/
 * myCouponId/used_count）是 camelCase。这里按原样声明，不在前端再转一次——转一次就得
 * 在每个消费页面重新对一遍账。
 */
import request from '@/utils/request'

/** 券模板：领券中心（scope=available）与管理台账共用一个结构，按 scope 取不同子集 */
export interface CouponTemplate {
  id: number
  coupon_name: string
  coupon_type: 1 | 2
  threshold: number | null
  amount: number | null
  discount: number | null
  total_count: number
  got_count: number
  start_at: string
  end_at: string
  status: 0 | 1
  remain: number
  /** 仅管理视图：这张券真正被花掉几张（领了不用等于活动没效果） */
  used_count?: number
  /** 仅 scope=available：我领过没有（含已核销的） */
  claimed?: boolean
  /** 仅 scope=available：我手上「未使用」那张的 user_coupon.id，下单要传它 */
  myCouponId?: number | null
}

/** 我的券包里的一张券。status 的 4/5 是读时推导的，库里只有 1 未使用 / 2 已核销 */
export interface MyCoupon {
  id: number
  couponId: number
  couponName: string
  couponType: 1 | 2
  typeName: string
  threshold: number
  amount: number | null
  discount: number | null
  ruleText: string
  startAt: string
  endAt: string
  status: 1 | 2 | 4 | 5
  statusName: string
  expired: boolean
  gotAt: string | null
  usedAt: string | null
  orderId: number | null
  orderNo: string | null
}

export interface CouponForm {
  couponName: string
  couponType: 1 | 2
  threshold: number
  amount?: number
  discount?: number
  totalCount: number
  startAt: string
  endAt: string
}

/** 领券中心：在架、在有效期且还有余量的券，带当前用户的领取状态 */
export const availableCouponsApi = () =>
  request.get<any, CouponTemplate[]>('/aux/coupons', { params: { scope: 'available' } })

export const claimCouponApi = (cid: number) =>
  request.post<any, { id: number }>(`/aux/coupons/${cid}/claim`)

export const myCouponsApi = (status?: 1 | 2) =>
  request.get<any, { list: MyCoupon[] }>('/aux/coupons/mine', {
    params: status ? { status } : undefined,
  })

/** 营销台账（marketing:coupon） */
export const couponsAdminApi = () =>
  request.get<any, CouponTemplate[]>('/aux/coupons')

export const createCouponApi = (data: CouponForm) =>
  request.post<any, null>('/aux/coupons', data)

export const setCouponStatusApi = (cid: number, status: 0 | 1) =>
  request.put<any, null>(`/aux/coupons/${cid}`, { status })
