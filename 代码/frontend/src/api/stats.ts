/** D8 统计报表域接口（设计03 D8 / 设计06 M-12）
 *
 * 报表的数字全部由后端 stats_service 一次算完，页面只渲染：
 * kpi / kpiLabels / kpiKinds / sections 是页面、图表和 CSV 共用的同一份元数据：
 * 指标叫什么、哪一列是钱都由后端说，前端不拼第二套表头也不按字段名猜口径
 * （那正是"导出和屏幕对不上"的来源）。
 */
import { downloadCsv } from '@/utils/download'
import request from '@/utils/request'

export type ReportKey = 'overview' | 'merchant' | 'airspace'
/** 页面页签：三张固定口径报表 + 一张字段池拼出来的自定义表 */
export type TabKey = ReportKey | 'custom'

export interface ReportMeta {
  start: string
  end: string
  /** overview/merchant/airspace 是 day|month；自定义报表没选时间维度时为 none */
  grain: 'day' | 'month' | 'none'
  scope: 'platform' | 'merchant'
  dataset?: string
  datasetLabel?: string
  truncated?: boolean
}

export interface ReportColumn {
  key: string
  label: string
  /** 展示类型由后端下发：前端按字段名猜"哪列是钱"就是第二套口径（货量 kg 会被印成 ¥） */
  kind: 'text' | 'int' | 'money' | 'num'
}

export type ReportRow = Record<string, string | number | null>

export interface ReportSection {
  key: string
  title: string
  columns: ReportColumn[]
  rows: ReportRow[]
}

export interface ReportPayload {
  meta: ReportMeta
  kpi: Record<string, number>
  kpiLabels: Record<string, string>
  kpiKinds: Record<string, ReportColumn['kind']>
  sections: ReportSection[]
}

export interface CustomPayload {
  meta: ReportMeta
  table: ReportSection
}

export interface FieldPool {
  datasets: {
    key: string
    label: string
    dims: { key: string; label: string }[]
    measures: { key: string; label: string }[]
  }[]
  timeDims: { key: string; label: string }[]
}

export interface ReportQuery {
  start?: string
  end?: string
  grain?: 'day' | 'month'
  dataset?: string
  dims?: string
  metrics?: string
}

/** 综合运营口径：KPI + 趋势 + 状态/品类/违规分布 */
export const statsOverviewApi = (q: ReportQuery = {}) =>
  request<ReportPayload>('/stats/overview', { params: q })

/** 商家经营口径：平台出全量对比，商家员工出本商家一行 */
export const statsMerchantApi = (q: ReportQuery = {}) =>
  request<ReportPayload>('/stats/merchant', { params: q })

/** 空管监管口径：申报/通过率/审批时长/冲突率 + 空域穿行 */
export const statsAirspaceApi = (q: ReportQuery = {}) =>
  request<ReportPayload>('/stats/airspace', { params: q })

export const reportFieldsApi = () => request<FieldPool>('/reports/fields')

export const reportCustomApi = (q: ReportQuery) => request<CustomPayload>('/reports/custom', { params: q })

/** CSV 导出走 report:export（与"看一眼"的 report:view 分开授权）。
 * 空值不进查询串：导出的区间必须和页面上刚查的那一次完全一致，
 * 否则"导出对不上屏幕"这个老问题又回来了。 */
export const exportReportApi = (q: ReportQuery & { report: TabKey }, filename: string) => {
  const params: Record<string, unknown> = {}
  Object.entries(q).forEach(([k, v]) => {
    if (v !== undefined && v !== '') params[k] = v
  })
  return downloadCsv('/reports/export', params, filename)
}
