/** D3 调度审批域接口（后端 /api/dispatch/*、/api/flight-plans/*、/api/airspaces） */
import request from '@/utils/request'
import type { PageResult } from './order'

// ---------------- 资源与推荐 ----------------
export interface AircraftOption {
  id: number
  aircraftNo: string
  brand: string
  model: string
  status: number
  statusName: string
  maxLoadKg: number
  enduranceMin: number
  maxSpeed: number
  battery: number | null
  online: boolean
  healthScore: number
  currentVertiportId: number | null
  busy: boolean
  needBatteryPercent?: number
}

export interface PilotOption {
  id: number
  realName: string
  username: string
  phone: string | null
  busy: boolean
}

export interface VertiportOption {
  id: number
  stationNo: string
  stationName: string
  stationType: number
  lng: number
  lat: number
  address: string | null
  status: number
  statusName: string
  capacity: number
  occupiedCount: number
}

export interface DispatchResources {
  aircraft: AircraftOption[]
  pilots: PilotOption[]
  vertiports: VertiportOption[]
}

export interface WeatherInfo {
  windSpeed: number
  windDirection: number
  windDirectionName: string
  visibilityM: number
  temperature: number
  weatherDesc: string
  warningLevel: number
  warningLevelName: string
  fetchedAt: string
  flyable: boolean
  mock: boolean
}

export interface ComplianceInfo {
  forbidden: { id: number; airspaceName: string; airspaceType: number }[]
  limited: { id: number; airspaceName: string; altitudeMax: number | null; altitudeMin: number | null }[]
}

export interface RecommendResult {
  orderId: number
  orderNo: string
  recommended: {
    aircraftId: number | null
    pilotId: number | null
    takeoffVertiportId: number
    landingVertiportId: number
    route: { lng: number; lat: number }[]
    /** 调机返场航段（收件点→降落场），地图用浅金虚线 */
    routeReturn?: { lng: number; lat: number }[]
    distanceM: number
    durationSec: number
    altitudeM: number
    /** 本航线最严的高度上限与服务端给出的来源说明（分区限高 / 全局限高） */
    altitudeMax?: number | null
    altitudeLimitFrom?: string | null
    cruiseSpeed: number
    weather: WeatherInfo
    compliance: ComplianceInfo
  }
  options: DispatchResources
  warnings: string[]
  blocked: boolean
  reasons: string[]
}

// ---------------- 飞行计划 ----------------
export interface PlanOrderBrief {
  orderNo: string
  status: number
  statusName: string
  senderName: string
  senderAddress: string
  senderLng: number
  senderLat: number
  receiverName: string
  receiverAddress: string
  receiverLng: number
  receiverLat: number
  goodsType: string
  weightKg: number
  deliveryType: number
}

export interface ApprovalRecordVO {
  id: number
  result: number
  resultName: string
  opinion: string | null
  approverName: string | null
  createdAt: string
}

export interface FlightPlanVO {
  id: number
  planNo: string
  orderId: number
  orderNo: string
  merchantId: number
  status: number
  statusName: string
  aircraftId: number | null
  aircraftNo: string | null
  aircraftModel: string | null
  pilotId: number | null
  pilotName: string | null
  takeoffVertiportId: number | null
  takeoffVertiportName: string | null
  takeoffVertiportLng: number | null
  takeoffVertiportLat: number | null
  landingVertiportId: number | null
  landingVertiportName: string | null
  landingVertiportLng: number | null
  landingVertiportLat: number | null
  route: { lng: number; lat: number }[] | null
  /** 调机返场航段（收件点→降落场），地图用浅金虚线 */
  routeReturn?: { lng: number; lat: number }[]
  distanceM: number | null
  durationSec: number | null
  taskName: string | null
  purpose: string | null
  altitudeM: number | null
  cruiseSpeed: number | null
  weather: WeatherInfo | null
  planStartAt: string | null
  planEndAt: string | null
  submittedAt: string | null
  approveResult: number | null
  approveOpinion: string | null
  rejectReason: string | null
  assignedAt: string | null
  actualTakeoffAt: string | null
  actualLandingAt: string | null
  currentLng: number | null
  currentLat: number | null
  currentAltitude: number | null
  currentSpeed: number | null
  currentBattery: number | null
  lastReportAt: string | null
  remark: string | null
  createdAt: string
  approvals?: ApprovalRecordVO[]
  order?: PlanOrderBrief
}

export interface PlanPage extends PageResult<FlightPlanVO> {
  statusCounts: Record<number, number>
}

export interface PlanPayload {
  orderId: number
  aircraftId: number
  pilotId: number
  takeoffVertiportId: number
  landingVertiportId: number
  altitudeM?: number
  cruiseSpeed?: number
  planStartAt?: string | null
  taskName?: string
  purpose?: string
  remark?: string
}

export interface AirspaceVO {
  id: number
  airspaceNo: string
  airspaceName: string
  airspaceType: number
  typeName: string
  geometry: { type: string; coordinates: number[][][] }
  altitudeMin: number | null
  altitudeMax: number | null
  regionName: string | null
  controlStartAt: string | null
  controlEndAt: string | null
  controlReason: string | null
}

export interface MockAdapterAck {
  accepted?: boolean
  applyNo?: string
  receivedAt?: string
  delivered?: boolean
  commandNo?: string
  ackAt?: string
}

export const getResourcesApi = () =>
  request.get<any, DispatchResources>('/dispatch/resources')

export const recommendApi = (orderId: number) =>
  request.post<any, RecommendResult>('/dispatch/recommend', { orderId })

export const createPlanApi = (data: PlanPayload) =>
  request.post<any, FlightPlanVO>('/flight-plans', data)

export const listPlansApi = (params: {
  page?: number
  pageSize?: number
  status?: number | ''
  keyword?: string
  mine?: boolean
}) => request.get<any, PlanPage>('/flight-plans', { params })

export const getPlanApi = (id: number | string) =>
  request.get<any, FlightPlanVO>(`/flight-plans/${id}`)

export const submitPlanApi = (id: number) =>
  request.post<any, MockAdapterAck>(`/flight-plans/${id}/submit`)

export const approvePlanApi = (id: number, opinion?: string) =>
  request.post<any, null>(`/flight-plans/${id}/approve`, { opinion })

export const rejectPlanApi = (id: number, reason: string) =>
  request.post<any, null>(`/flight-plans/${id}/reject`, { reason })

export const batchApproveApi = (ids: number[], opinion?: string) =>
  request.post<any, { count: number; planNos: string[] }>('/flight-plans/batch-approve', { ids, opinion })

export const assignPlanApi = (id: number) =>
  request.post<any, MockAdapterAck>(`/flight-plans/${id}/assign`)

export const takeoffPlanApi = (id: number) =>
  request.post<any, MockAdapterAck>(`/flight-plans/${id}/takeoff`)

export const landPlanApi = (id: number) =>
  request.post<any, MockAdapterAck>(`/flight-plans/${id}/land`)

export const cancelPlanApi = (id: number, reason?: string) =>
  request.post<any, null>(`/flight-plans/${id}/cancel`, { reason })

export const listAirspacesApi = (type?: number) =>
  request.get<any, { list: AirspaceVO[]; total: number }>('/airspaces', { params: { type } })

/** 调度台切换起降场后实时重算航程（含本航线的最严限高，服务端算，前端不再判一遍） */
export interface RouteCalcVO {
  distanceM: number; durationSec: number; route: any[]; routeReturn: any[]
  altitudeMax: number | null; altitudeLimitFrom: string; forbiddenAirspaces: string[]
}
export const recalcRouteApi = (data: { orderId: number; takeoffVertiportId: number; landingVertiportId: number }) =>
  request.post<any, RouteCalcVO>('/dispatch/recalc', data)
