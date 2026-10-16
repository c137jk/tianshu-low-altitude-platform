/** D5 空域合规域接口（/api/airspaces*、/api/geofences*、/api/aux/violations*）
 *
 * 需求 4.2.4 空域合规运营：空域可视化与规则查询、空域申报审核、电子围栏、违规处理。
 */
import request from '@/utils/request'

export interface GeoPolygon { type: 'Polygon'; coordinates: number[][][] }

export interface AirspaceAdminVO {
  id: number
  airspaceNo: string
  airspaceName: string
  airspaceType: number
  typeName: string
  source: number
  sourceName: string
  geometry: GeoPolygon
  altitudeMin: number | null
  altitudeMax: number | null
  regionName: string | null
  status: number
  statusName: string
  auditStatus: number
  auditName: string
  auditedBy: string | null
  auditedAt: string | null
  auditOpinion: string | null
  controlStartAt: string | null
  controlEndAt: string | null
  controlReason: string | null
  createdAt: string | null
}

export interface AirspacePayload {
  airspaceName: string
  airspaceType: number
  geometry: GeoPolygon
  altitudeMin?: number | null
  altitudeMax?: number | null
  regionName?: string
  controlReason?: string
  controlStartAt?: string | null
  controlEndAt?: string | null
}

export interface GeofenceVO {
  id: number
  fenceName: string
  merchantId: number | null
  merchantName: string
  geometry: GeoPolygon
  fenceType: number
  typeName: string
  altitudeLimit: number | null
  warnDistanceM: number | null
  status: number
  statusName: string
  createdAt: string | null
}

export interface SyncResult {
  syncedAt: string
  expired: string[]
  stat: { source: string; status: string; count: number }[]
}

export const listAirspacesAdminApi = (params?: { all?: 1; type?: number; keyword?: string }) =>
  request<{ list: AirspaceAdminVO[]; total: number }>('/airspaces', { params })

export const createAirspaceApi = (d: AirspacePayload) =>
  request<{ id: number; airspaceNo: string }>('/airspaces', { method: 'POST', data: d })

export const updateAirspaceApi = (id: number, d: Record<string, any>) =>
  request<{ id: number; auditStatus: number }>(`/airspaces/${id}`, { method: 'PUT', data: d })

export const auditAirspaceApi = (id: number, result: 1 | 2, opinion?: string) =>
  request<{ id: number }>(`/airspaces/${id}/audit`, { method: 'POST', data: { result, opinion } })

export const syncAirspacesApi = () =>
  request<SyncResult>('/airspaces/sync', { method: 'POST' })

/** 空域下架（逻辑删除）：与审核同级权限，空管下发来源与有在飞航线的空域会被 409 挡回 */
export const deleteAirspaceApi = (id: number) =>
  request<{ id: number }>(`/airspaces/${id}`, { method: 'DELETE' })

export const listGeofencesApi = () =>
  request<{ list: GeofenceVO[]; total: number }>('/geofences')

/** 电子围栏「适用商家」下拉（平台可选任一商家，商家账号只拿到自己） */
export const listFenceOwnersApi = () =>
  request<{ id: number; name: string }[]>('/geofences/owners')

export const createGeofenceApi = (d: Record<string, any>) =>
  request<{ id: number; notified: number }>('/geofences', { method: 'POST', data: d })

export const updateGeofenceApi = (id: number, d: Record<string, any>) =>
  request<{ id: number; notified: number }>(`/geofences/${id}`, { method: 'PUT', data: d })

export const deleteGeofenceApi = (id: number) =>
  request<{ id: number }>(`/geofences/${id}`, { method: 'DELETE' })
