/** 站内信 → 落地页（点一条消息要能办这件事，不是只标个已读）。
 *
 * 为什么要这张表：预警按责任人发（资质到期发本人、驳回发商家、失联发运维），
 * 但收信人不一定有对应模块的权限；管控台的路由是"有权限才注册"的，
 * push 一个没注册的路径会命中 404 兜底、被守卫送回首页，表现就是"点了没反应"。
 * 所以每条消息给一组候选页，第一条当前账号真进得去的才算落点；一条都没有就不跳。
 *
 * anyOf 为空 = 该路由不挂权限、人人都进得去（个人中心 /admin/profile）。
 * 这里的权限点名单必须与 router/index.ts 注册该页面时的条件逐字一致。
 */

export interface MsgRoute {
  path: string
  /** 落地页叫什么：跳不过去时告诉用户该找谁 */
  title: string
  anyOf: string[]
  /** 带 biz_id 时把 path 当详情前缀用（/admin/order/12）；没有 id 就跳过这条候选 */
  byId?: boolean
}

const PLAN_DETAIL = '/admin/dispatch/plans'
const ORDER_DETAIL = '/admin/order'
const QUAL_ROUTES: MsgRoute[] = [
  { path: '/admin/resource/personnel', title: '人员管理 · 资质台账', anyOf: ['resource:qualification'] },
  { path: '/admin/profile', title: '我的资质与值班', anyOf: [] },
]

/** 同一 biz_type 的候选按"能处理这件事的人优先"排序 */
const ROUTES: Record<string, MsgRoute[]> = {
// 三种资质消息同一张落点表：审核岗（resource:qualification）进台账复审，
  // 其余人——包括被预警的机长本人——退到个人中心看自己那一份，都不会落到 403
  qualification: QUAL_ROUTES,
  qual_expiring: QUAL_ROUTES,
  qual_expired: QUAL_ROUTES,
  schedule: [
    { path: '/admin/profile', title: '我的资质与值班', anyOf: [] },
    { path: '/admin/resource/schedule', title: '排班管理', anyOf: ['resource:schedule'] },
  ],
  // 机长接到的"派给你了"：有 plan_id 直接进那条计划，没有就退回任务台
  plan_assign: [
    { path: PLAN_DETAIL, title: '飞行任务', anyOf: ['dispatch:execute', 'dispatch:plan', 'approval:handle'], byId: true },
    { path: '/admin/dispatch/missions', title: '我的飞行任务', anyOf: ['dispatch:execute'] },
  ],
  plan_approve: [{ path: PLAN_DETAIL, title: '飞行计划', anyOf: ['dispatch:plan', 'approval:handle', 'dispatch:execute'], byId: true }],
  plan_reject: [{ path: PLAN_DETAIL, title: '飞行计划', anyOf: ['dispatch:plan', 'approval:handle', 'dispatch:execute'], byId: true }],
  violation: [{ path: '/admin/airspace/violations', title: '违规处理', anyOf: ['monitor:violation'] }],
  violation_penalty: [{ path: '/admin/airspace/violations', title: '违规处理', anyOf: ['monitor:violation'] }],
  reservation: [{ path: '/admin/resource/vertiports', title: '场站管理 · 机位预约', anyOf: ['resource:vertiport'] }],
  device_lost: [{ path: '/admin/monitor/devices', title: '设备在线', anyOf: ['monitor:device'] }],
  device_recover: [{ path: '/admin/monitor/devices', title: '设备在线', anyOf: ['monitor:device'] }],
  emergency: [{ path: '/admin/monitor/realtime', title: '实时监控', anyOf: ['monitor:realtime'] }],
  work_order: [{ path: '/admin/service/center', title: '客服中心', anyOf: ['service:workorder'] }],
  coupon_offline: [{ path: '/admin/marketing/center', title: '营销中心', anyOf: ['marketing:coupon'] }],
  merchant_audit: [{ path: '/admin/system/users?tab=merchants', title: '商家入驻审核', anyOf: ['merchant:audit'] }],
  order_new: [{ path: ORDER_DETAIL, title: '订单详情', anyOf: ['order:list'], byId: true }],
  order_take_timeout: [{ path: ORDER_DETAIL, title: '订单详情', anyOf: ['order:list'], byId: true }],
  order_cancel: [{ path: ORDER_DETAIL, title: '订单详情', anyOf: ['order:list'], byId: true }],
  order_accept: [{ path: ORDER_DETAIL, title: '订单详情', anyOf: ['order:list'], byId: true }],
  order_reject: [{ path: ORDER_DETAIL, title: '订单详情', anyOf: ['order:list'], byId: true }],
  order_finish: [{ path: ORDER_DETAIL, title: '订单详情', anyOf: ['order:list'], byId: true }],
  order: [{ path: ORDER_DETAIL, title: '订单详情', anyOf: ['order:list'], byId: true }],
  order_status: [{ path: ORDER_DETAIL, title: '订单详情', anyOf: ['order:list'], byId: true }],
}

/** 当前账号真进得去的落点；一条都不满足返回 null（调用方只标已读，不跳页） */
export function msgRoute(bizType: string | null | undefined, bizId: number | null | undefined,
  has: (code: string) => boolean): MsgRoute | null {
  const cands = bizType ? ROUTES[bizType] : undefined
  if (!cands) return null
  for (const c of cands) {
    if (c.anyOf.length && !c.anyOf.some(has)) continue
    if (c.byId && !bizId) continue
    return { ...c, path: c.byId ? `${c.path}/${bizId}` : c.path }
  }
  return null
}
