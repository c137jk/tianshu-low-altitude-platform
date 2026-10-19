/** 业务字典（与后端 order_service.ORDER_STATUS 等同口径，前端展示用） */

// 订单状态：0待支付 1待接单 2待调度 3待审批 4待起飞 5飞行中 6已送达 7已完成 8已取消
export const ORDER_STATUS_MAP: Record<number, { name: string; type: 'info' | 'warning' | 'primary' | 'success' | 'danger' }> = {
  0: { name: '待支付', type: 'danger' },
  1: { name: '待接单', type: 'warning' },
  2: { name: '待调度', type: 'warning' },
  3: { name: '待审批', type: 'warning' },
  4: { name: '待起飞', type: 'primary' },
  5: { name: '飞行中', type: 'primary' },
  6: { name: '已送达', type: 'success' },
  7: { name: '已完成', type: 'success' },
  8: { name: '已取消', type: 'info' },
}

// 客户列表页 Tab（value '' 表示全部）
export const ORDER_TABS = [
  { label: '全部', value: '' as const },
  { label: '待支付', value: 0 as const },
  { label: '进行中', value: 'active' as const },
  { label: '已完成', value: 7 as const },
  { label: '已取消', value: 8 as const },
]

// 进行中状态集合（Tab 用，后端 active 状态在前端过滤）
export const ACTIVE_STATUS = [1, 2, 3, 4, 5, 6]

// 客户视角进度条节点（设计06 C-3/C-4）
export const ORDER_STEPS = ['已下单', '已支付', '调度审批', '配送中', '已送达']

export function stepIndex(status: number): number {
  // 0 待支付 → 0；1~4 → 1；3 审批 → 2；4/5 → 3；6/7 → 4；取消单独展示
  if (status === 0) return 0
  if (status === 1 || status === 2) return 1
  if (status === 3) return 2
  if (status === 4 || status === 5) return 3
  if (status === 6 || status === 7) return 4
  return 0
}

export const GOODS_TYPES = ['文件证件', '数码产品', '餐饮外卖', '生鲜水果', '医药急件', '礼品', '其他']

export const PAY_METHODS = [
  { value: 1, label: '微信支付（Mock）' },
  { value: 2, label: '支付宝（Mock）' },
  { value: 3, label: '对公转账（Mock）' },
]

export function yuan(n: number | null | undefined): string {
  return `¥${Number(n ?? 0).toFixed(2)}`
}

// 飞行计划状态：0待申报 1待审批 2已批准待执行 3执行中 4已完成 5已驳回 6已取消
export const PLAN_STATUS_MAP: Record<number, { name: string; type: 'info' | 'warning' | 'primary' | 'success' | 'danger' }> = {
  0: { name: '待申报', type: 'info' },
  1: { name: '待审批', type: 'warning' },
  2: { name: '待执行', type: 'primary' },
  3: { name: '执行中', type: 'primary' },
  4: { name: '已完成', type: 'success' },
  5: { name: '已驳回', type: 'danger' },
  6: { name: '已取消', type: 'info' },
}

// 计划列表 Tab（value '' 表示全部）
export const PLAN_TABS = [
  { label: '全部', value: '' as const },
  { label: '待申报', value: 0 as const },
  { label: '待审批', value: 1 as const },
  { label: '待执行', value: 2 as const },
  { label: '执行中', value: 3 as const },
  { label: '已完成', value: 4 as const },
  { label: '已驳回', value: 5 as const },
]

// 空域类型：1禁飞 2限飞 3可飞 4临时管制
export const AIRSPACE_TYPE_MAP: Record<number, { name: string; color: string }> = {
  1: { name: '禁飞区', color: '#C0392B' },
  2: { name: '限飞区', color: '#D39A2B' },
  3: { name: '可飞区', color: '#2E7D52' },
  4: { name: '临时管制', color: '#8E44AD' },
}

/** 空域高度区间的展示口径。
 *  种子数据里禁飞区的 altitude_max 是 0，直接拼 `0 ~ 0 m` 看着像数据坏了：
 *  禁飞区本来就没有"可用高度"，而限高留空/0 在别的类型上是"不限高"的意思。 */
export function fmtAltitudeBand(
  min?: number | null, max?: number | null, type?: number,
): string {
  if (type === 1) return '禁飞 · 无可用高度'
  if (max === null || max === undefined || max === 0) return `${min ?? 0} m 起 · 不限高`
  return `${min ?? 0} ~ ${max} m`
}

// 管控台订单列表 Tab（按 D3 合流状态机）
export const STAFF_ORDER_TABS = [
  { label: '待接单', value: 1 as const },
  { label: '待调度', value: 2 as const },
  { label: '待审批', value: 3 as const },
  { label: '待起飞', value: 4 as const },
  { label: '飞行中', value: 5 as const },
  { label: '已送达', value: 6 as const },
  { label: '全部', value: '' as const },
]

/** 排班班次：取值口径来自 staff_schedule.shift_type 的 DDL 注释（1早班 2中班 3晚班 4全天）。
 *  cross=true 表示结束时间落在次日（晚班跨零点）；起止时刻集中在这里定义，
 *  以前"排今天"把早班时间写死在按钮里，所以点出来永远是 07:00-15:00 一条。 */
export const SHIFT_TYPES: {
  value: number; label: string; start: string; end: string; cross: boolean;
  tag: 'primary' | 'warning' | 'danger' | 'success';
}[] = [
  { value: 1, label: '早班', start: '07:00', end: '15:00', cross: false, tag: 'primary' },
  { value: 2, label: '中班', start: '15:00', end: '23:00', cross: false, tag: 'warning' },
  { value: 3, label: '晚班', start: '23:00', end: '07:00', cross: true, tag: 'danger' },
  { value: 4, label: '全天', start: '08:00', end: '20:00', cross: false, tag: 'success' },
]

export const SHIFT_NAME: Record<number, string> =
  Object.fromEntries(SHIFT_TYPES.map((s) => [s.value, s.label]))

/** 排班状态：1计划 2已出勤 3请假 4缺勤（staff_schedule.status） */
export const SCHEDULE_STATUS_MAP: Record<number, { name: string; type: 'info' | 'success' | 'warning' | 'danger' }> = {
  1: { name: '计划', type: 'info' },
  2: { name: '已出勤', type: 'success' },
  3: { name: '请假', type: 'warning' },
  4: { name: '缺勤', type: 'danger' },
}

/** 按班次拼出起止时刻（'YYYY-MM-DD HH:mm:ss'）。晚班结束日 +1 天。 */
export function shiftRange(date: string, type: number): { start: string; end: string } | null {
  const s = SHIFT_TYPES.find((x) => x.value === type)
  if (!s || !date) return null
  let endDay = date
  if (s.cross) {
    const d = new Date(`${date}T00:00:00`)
    d.setDate(d.getDate() + 1)
    endDay = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }
  return { start: `${date} ${s.start}:00`, end: `${endDay} ${s.end}:00` }
}

/** 资质类别（qualification.qual_type）：取值与后端 app/blueprints/resource.py 的 QUAL_TYPES
 *  逐字一致，那边是白名单，这里多一个或少一个都会被 400 拒掉。 */
export const QUAL_TYPES = [
  { value: 'license', label: '营业执照' },
  { value: 'permit', label: '运营许可' },
  { value: 'airworthiness', label: '适航证' },
  { value: 'driver', label: '驾驶证' },
  { value: 'training', label: '培训证' },
]
export const QUAL_TYPE_NAME: Record<string, string> =
  Object.fromEntries(QUAL_TYPES.map((t) => [t.value, t.label]))

/** 资质类别对应的主体类型：与后端 resource.QUAL_TYPE_SUBJECT 同口径（执照归商家、
 *  适航证归飞行器、驾驶/培训证归人员）。新建口一开，类别和主体就必须成对，
 *  否则台账里会出现"某个机长名下一本营业执照"这种没法复审的行。 */
export const QUAL_TYPE_SUBJECT: Record<string, number> = {
  license: 1, permit: 1, airworthiness: 3, driver: 2, training: 2,
}

/** 某个主体类型能登记的资质类别 */
export function qualTypesOf(subjectType: number) {
  return QUAL_TYPES.filter((t) => QUAL_TYPE_SUBJECT[t.value] === subjectType)
}

/** 资质状态 0待审核 1有效 2即将到期 3已过期 4已驳回（后端 QUAL_STATUS 同口径）。
 *  0 是新建的唯一入口态——包括审核岗自己登记的，复审通过才置 1，所以它排在最前面。 */
export const QUAL_STATUS_MAP: Record<number,
  { name: string; type: 'info' | 'success' | 'warning' | 'danger' }> = {
  0: { name: '待审核', type: 'warning' },
  1: { name: '有效', type: 'success' },
  2: { name: '即将到期', type: 'warning' },
  3: { name: '已过期', type: 'danger' },
  4: { name: '已驳回', type: 'info' },
}

/** 秒 → "x分y秒" / 距离米 → km 文案 */
export function fmtDuration(sec: number | null | undefined): string {
  const s = Number(sec ?? 0)
  const m = Math.floor(s / 60)
  return m > 0 ? `${m}分${s % 60}秒` : `${s}秒`
}

export function fmtDistance(m: number | null | undefined): string {
  const v = Number(m ?? 0)
  return v >= 1000 ? `${(v / 1000).toFixed(2)} km` : `${Math.round(v)} m`
}
