/** W5 低空资源域接口（/api/resource/*） */
import request from '@/utils/request'
import { viewStoredFile } from '@/utils/download'

export interface VertiportVO {
  id: number; stationNo: string; stationName: string
  stationType: number; typeName: string
  lng: number; lat: number; address: string | null
  capacity: number; chargerTotal: number; chargerAvailable: number; occupiedCount: number
  status: number; statusName: string
}

export interface AircraftVO {
  id: number; aircraftNo: string; model: string; modelName: string | null; brand: string | null
  merchantId: number; merchantName: string | null
  enduranceMin: number; maxLoadKg: number; maxAltitude: number
  status: number; statusName: string; battery: number; online: number
  healthScore: number; taskCount: number; totalFlightMin: number; faultCount: number
  vertiportName: string | null; nextMaintainAt: string | null
  lng: number | null; lat: number | null
}

export const listVertiportsApi = () =>
  request<{ list: VertiportVO[]; total: number }>('/resource/vertiports')

export const listAircraftApi = () =>
  request<{ list: AircraftVO[]; total: number }>('/resource/aircraft')

/** 飞行器详情（M-52）：台账 + 在飞任务 + 维保历史 + 飞行履历 */
export interface AircraftDetailVO {
  aircraft: {
    id: number; aircraftNo: string; serialNo: string | null; brand: string | null; model: string | null
    modelName: string; merchantId: number; merchantName: string | null
    enduranceMin: number | null; maxLoadKg: number; maxSpeed: number; maxAltitude: number | null
    status: number; statusName: string; battery: number; online: number
    healthScore: number; taskCount: number; totalFlightMin: number; faultCount: number
    purchaseAt: string | null; lastMaintainAt: string | null; nextMaintainAt: string | null
    maintainOverdue: boolean; vertiportId: number | null; vertiportName: string | null
    lng: number | null; lat: number | null; updatedAt: string | null
  }
  flying: {
    id: number; planNo: string; taskName: string | null; altitudeM: number | null; battery: number | null
    lng: number | null; lat: number | null; takeoffAt: string | null; lastReportAt: string | null
  } | null
  maintenance: {
    id: number; recordType: number; typeName: string; faultType: string | null; description: string | null
    status: number; statusName: string; result: string | null; handlerName: string | null
    startedAt: string | null; finishedAt: string | null; flightMinutesAt: number | null
  }[]
  flights: {
    id: number; planNo: string; taskName: string | null; orderNo: string | null
    status: number; statusName: string; altitudeM: number | null; distanceKm: number
    durationMin: number; actual: boolean; takeoffAt: string | null; landingAt: string | null
    route: string; violations: number
  }[]
  stats: { plans: number; done: number; aborted: number; landed: number; openViolations: number; maintenanceOpen: number }
}
export const aircraftDetailApi = (id: number) =>
  request<AircraftDetailVO>(`/resource/aircraft/${id}`)


export interface PersonnelVO {
  userId: number; username: string; realName: string | null
  accountStatus: number; merchantName: string | null; roleNames: string | null
  taskTotal: number; taskDone: number; flying: number; flownHours: number
  qualTotal: number; qualExpired: number; nearestExpireAt: string | null
  qualExpiring: boolean; onDutyToday: boolean; shiftName: string | null
}
export const listPersonnelApi = () =>
  request<{ list: PersonnelVO[]; total: number; qualWarnDays: number }>('/resource/personnel')

export interface QualVO {
  id: number; subjectType: number; subjectTypeName: string; subjectId: number
  subjectName: string; merchantName: string | null
  qualType: string; qualName: string; fileUrl: string | null
  issueAt: string | null; expireAt: string | null
  status: number; statusName: string
  auditOpinion: string | null; auditedAt: string | null
}
export const listQualificationsApi = (subjectType?: number) =>
  request<{ list: QualVO[]; total: number }>('/resource/qualifications',
    { params: subjectType ? { subjectType } : {} })
/** 资质复审：result 1 通过（置为有效）/ 4 驳回。只有 resource:qualification 能调，读台账不需要 */
export const auditQualificationApi = (id: number, result: 1 | 4, opinion?: string) =>
  request(`/resource/qualifications/${id}/audit`, { method: 'POST', data: { result, opinion } })

/** 新建结果：无论谁递的，都停在 status=0 待审核，有效只有 audit 一个出口 */
export interface QualCreateVO { id: number; status: number; statusName: string }

/**
 * 资质新建走 multipart，不是 JSON：证照原件本身就在请求体里。
 * timeout 放宽到 60s（service 默认 10s，一张手机拍的证件照很容易超）。
 */
const QUAL_FORM_CFG = { method: 'POST', timeout: 60000 } as const

/** 管理侧代登记（需要 resource:qualification）：商家/飞行器没有"本人"，只能代登记 */
export const createQualificationApi = (form: FormData) =>
  request<QualCreateVO>('/resource/qualifications', { ...QUAL_FORM_CFG, data: form })

/** 本人自助上传（subjectType/subjectId 由服务端写死为 (2, 本人)），运营侧账号都可用 */
export const uploadMyQualApi = (form: FormData) =>
  request<QualCreateVO>('/resource/qualifications/mine', { ...QUAL_FORM_CFG, data: form })

/** 打开证照原件：地址是接口而不是静态路径，放行名单与资质台账同一份 */
export const openQualFileApi = (id: number, qualName: string) =>
  viewStoredFile(`/resource/qualifications/${id}/file`, `${qualName}·证照原件`)

/**
 * 本人读自己那份原件。
 *
 * 为什么另有一条地址：台账那条口认的是 qualification / system:user / schedule / aircraft
 * 四个权限点，机长一个都没有，装饰器就先把请求拦在门外了——视图里再判"是不是本人"也轮不到。
 * 这条自助口的主体条件在服务端 WHERE 里，读别人的 id 只会拿到 404。
 */
export const openMyQualFileApi = (id: number, qualName: string) =>
  viewStoredFile(`/resource/qualifications/mine/${id}/file`, `${qualName}·证照原件`)

/** 我的资质与值班（个人中心自助读，不需要任何 resource:* 权限点） */
export interface MyQualVO {
  id: number; qualType: string; qualName: string; fileUrl: string | null
  issueAt: string | null; expireAt: string | null
  status: number; statusName: string; daysLeft: number | null; warn: boolean
  auditOpinion: string | null
}
export interface MyShiftVO {
  id: number; shiftDate: string; shiftType: number; shiftName: string | null
  startTime: string | null; endTime: string | null
  area: string | null; status: number; statusName: string | null
}
export interface MyDutyVO {
  qualifications: MyQualVO[]
  shifts: MyShiftVO[]
  range: { from: string; to: string }
  dutyBoard: { shiftType: number; shiftName: string; count: number }[]
  todayOnDuty: boolean; todayOnDutyTotal: number; qualWarnDays: number
}
export const myDutyApi = (from?: string, to?: string) =>
  request<MyDutyVO>('/resource/my-duty',
    { params: from && to ? { from, to } : {} })


export const createVertiportApi = (d: Record<string, any>) => request('/resource/vertiports', { method: 'POST', data: d })
export const updateVertiportApi = (id: number, d: Record<string, any>) => request(`/resource/vertiports/${id}`, { method: 'PUT', data: d })
export const createAircraftApi = (d: Record<string, any>) => request('/resource/aircraft', { method: 'POST', data: d })
/** 飞行器必须落到某个商家（aircraft.merchant_id 非空），新增时用这个下拉 */
export const listMerchantsApi = () =>
  request<{ id: number; name: string }[]>('/resource/merchants')
export const updateAircraftApi = (id: number, d: Record<string, any>) => request(`/resource/aircraft/${id}`, { method: 'PUT', data: d })
/** 一键充换电：全场飞行器电量回 100% 并置为闲置（有在飞任务时后端会 409 拒绝） */
export const chargeAllAircraftApi = () => request<{ count: number }>('/resource/aircraft/charge-all', { method: 'POST' })

/** 场站预约（M-55）：status 1待审 2已通过 3已驳回 4已完成 5已取消；purpose 1起降 2换电 3充电 */
export interface ReservationVO {
  id: number; reservationNo: string
  stationId: number; stationName: string
  merchantId: number; merchantName: string | null
  userId: number; userName: string | null
  aircraftId: number | null; aircraftNo: string | null
  purpose: number; purposeName: string | null
  startAt: string; endAt: string
  status: number; statusName: string
  auditOpinion: string | null; createdAt: string | null
}
export interface ReservationQuery {
  stationId?: number; status?: number; date?: string
}
export const listReservationsApi = (q: ReservationQuery = {}) =>
  request<{ list: ReservationVO[]; total: number }>('/resource/reservations',
    { params: Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined && v !== '')) })
export const createReservationApi = (d: Record<string, any>) =>
  request<{ id: number; reservationNo: string }>('/resource/reservations', { method: 'POST', data: d })
/** 机位放行：审核时后端会按当前机位余量再判一次，满了会 409 */
export const auditReservationApi = (id: number, result: 2 | 3, opinion?: string) =>
  request(`/resource/reservations/${id}/audit`, { method: 'POST', data: { result, opinion } })
export const cancelReservationApi = (id: number) =>
  request(`/resource/reservations/${id}/cancel`, { method: 'POST' })
export const finishReservationApi = (id: number) =>
  request(`/resource/reservations/${id}/finish`, { method: 'POST' })

