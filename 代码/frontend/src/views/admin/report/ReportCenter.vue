<template>
  <div>
    <!-- 四类口径共用同一条筛选栏：切页签不丢区间，导出带的也是这一份参数 -->
    <div class="ts-card bar">
      <el-radio-group v-model="tab" size="small" @change="reload">
        <el-radio-button v-for="t in TABS" :key="t.key" :value="t.key">{{ t.label }}</el-radio-button>
      </el-radio-group>
      <el-date-picker
        v-model="dates" type="daterange" value-format="YYYY-MM-DD" unlink-panels
        start-placeholder="起始日" end-placeholder="截止日" style="width: 236px" @change="reload"
      />
      <el-radio-group v-if="tab !== 'custom'" v-model="grain" size="small" @change="reload">
        <el-radio-button value="day">按日</el-radio-button>
        <el-radio-button value="month">按月</el-radio-button>
      </el-radio-group>
      <el-button :icon="Refresh" @click="reload">查询</el-button>
      <span class="flex-gap" />
      <span v-if="meta" class="sub">区间 {{ meta.start }} ~ {{ meta.end }} · {{ grainName }}</span>
      <el-tag size="small" :type="meta?.scope === 'merchant' ? 'warning' : 'info'" effect="plain">
        {{ meta?.scope === 'merchant' ? '本商家口径' : '全平台口径' }}
      </el-tag>
      <el-button
        v-if="canExport" :icon="Download" :loading="exporting"
        :disabled="!meta" @click="doExport"
      >导出 CSV</el-button>
    </div>

    <div v-loading="loading" class="body">
      <template v-if="tab === 'custom'">
        <div class="ts-card pool">
          <span class="lbl">数据集</span>
          <el-select v-model="dsKey" size="small" style="width: 128px" @change="onDataset">
            <el-option v-for="d in datasets" :key="d.key" :label="d.label" :value="d.key" />
          </el-select>
          <span class="lbl">维度</span>
          <el-select v-model="dims" multiple :multiple-limit="4" size="small" style="width: 320px"
            placeholder="至少选一个，比如「按日」或「商家」" @change="reload">
            <el-option-group label="时间">
              <el-option v-for="t in pool?.timeDims || []" :key="t.key" :label="t.label" :value="t.key" />
            </el-option-group>
            <el-option-group label="业务维度">
              <el-option v-for="d in dimPool" :key="d.key" :label="d.label" :value="d.key" />
            </el-option-group>
          </el-select>
          <span class="lbl">指标</span>
          <el-select v-model="metrics" multiple size="small" style="width: 320px"
            placeholder="留空＝该数据集全部指标" @change="reload">
            <el-option v-for="m in measurePool" :key="m.key" :label="m.label" :value="m.key" />
          </el-select>
          <span class="flex-gap" />
          <span class="sub">维度封顶 4 个：再交叉下去这张表就没法读了</span>
        </div>
        <el-alert
          v-if="custom?.meta.truncated" class="tip" type="warning" :closable="false" show-icon
          title="分组数超过 500 已截断，请收窄区间或维度"
        />
      </template>

      <div v-if="kpiCells.length" class="kpis ts-card">
        <div v-for="k in kpiCells" :key="k.key" class="kpi">
          <div class="k">{{ k.label }}</div>
          <div class="v" :class="{ money: k.kind === 'money' }">{{ fmt(k.value, k.kind) }}</div>
        </div>
      </div>

      <div v-for="s in sections" :key="s.key" class="ts-card blk">
        <div class="blk-title">{{ s.title }}</div>
        <div v-if="chartType(s)" :ref="(el: any) => setChartEl(s.key, el)" class="chart" />
        <el-table
          v-if="s.rows.length" :data="s.rows" size="small" border stripe
          :max-height="s.key === 'trend' ? 260 : undefined" :row-class-name="rowClass"
        >
          <el-table-column
            v-for="c in s.columns" :key="c.key" :prop="c.key" :label="c.label"
            :min-width="colWidth(c)" :align="c.kind === 'text' ? 'left' : 'right'"
          >
            <template #default="{ row }">
              <span :class="c.kind === 'text' ? '' : 'num'">{{ fmtCell(row[c.key], c.kind) }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div v-else class="sub">区间内没有数据（换成按月或放宽区间试试）。</div>
      </div>

      <p class="sub note">
        金额只统计"已支付且未作废"的订单实付额，退款单不再计入营收；平台抽成按结算费率在订单上计提，
        所以已支付但尚未出账的单子也算得进来。页面、图表、导出 CSV 读的是后端同一份指标定义与列标签，
        三处不可能各说一套数。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Download } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import {
  statsOverviewApi, statsMerchantApi, statsAirspaceApi,
  reportFieldsApi, reportCustomApi, exportReportApi,
  type ReportPayload, type ReportSection, type ReportColumn, type ReportRow,
  type CustomPayload, type FieldPool, type ReportKey, type ReportQuery, type TabKey,
} from '@/api/stats'
import { yuan } from '@/constants/dict'
import { useUserStore } from '@/stores/user'

const TABS: { key: TabKey; label: string; report: ReportKey | 'custom' }[] = [
  { key: 'overview', label: '综合运营', report: 'overview' },
  { key: 'merchant', label: '商家经营', report: 'merchant' },
  { key: 'airspace', label: '空域合规', report: 'airspace' },
  { key: 'custom', label: '自定义报表', report: 'custom' },
]
const PALETTE = ['#23406E', '#37d67a', '#c8a45c', '#5b8ff9', '#c0392b', '#8a93a6']

const userStore = useUserStore()
const canExport = computed(() => userStore.hasPerm('report:export'))

const tab = ref<TabKey>('overview')
const dates = ref<[string, string] | null>(null)
const grain = ref<'day' | 'month'>('day')
const loading = ref(false)
const exporting = ref(false)
const report = ref<ReportPayload | null>(null)
const custom = ref<CustomPayload | null>(null)

const pool = ref<FieldPool | null>(null)
const dsKey = ref('order')
const dims = ref<string[]>(['day'])
const metrics = ref<string[]>([])

const meta = computed(() => (tab.value === 'custom' ? custom.value?.meta : report.value?.meta))
const grainName = computed(() => {
  const g = meta.value?.grain
  return g === 'month' ? '按月' : g === 'none' ? '不按时间分桶' : '按日'
})
const kpiCells = computed(() => {
  const r = report.value
  if (!r) return []
  return Object.entries(r.kpi).map(([key, value]) => ({
    key, label: r.kpiLabels[key] || key, kind: r.kpiKinds[key] || 'num', value,
  }))
})
const sections = computed<ReportSection[]>(() => {
  if (tab.value === 'custom') return custom.value ? [custom.value.table] : []
  return report.value?.sections || []
})

const datasets = computed(() => pool.value?.datasets || [])
const currentDs = computed(() => datasets.value.find((d) => d.key === dsKey.value))
const dimPool = computed(() => currentDs.value?.dims || [])
const measurePool = computed(() => currentDs.value?.measures || [])

function fmt(v: number | null | undefined, kind: ReportColumn['kind']): string {
  const n = Number(v ?? 0)
  if (kind === 'money') return yuan(n)
  if (kind === 'int') return n.toLocaleString('zh-CN')
  return String(v ?? 0)
}
function fmtCell(v: ReportRow[string], kind: ReportColumn['kind']): string {
  if (v === null || v === undefined || v === '') return '—'
  return typeof v === 'number' ? fmt(v, kind) : String(v)
}
function colWidth(c: ReportColumn): number {
  if (c.kind === 'text') return Math.max(110, c.label.length * 14 + 24)
  return Math.max(92, c.label.length * 12 + 26)
}
/** 商家明细的合计行是单独加总的（不是拿四舍五入后的行值相加），要在表里认得出来 */
function rowClass({ row }: { row: ReportRow }): string {
  const first = Object.values(row)[0]
  return typeof first === 'string' && first.startsWith('合计') ? 'row-total' : ''
}

// ---------------------------------------------------------------- 图表
const chartEls: Record<string, HTMLElement | null> = {}
const charts: Record<string, echarts.ECharts> = {}

function setChartEl(key: string, el: HTMLElement | null) {
  chartEls[key] = el
}
function disposeCharts() {
  Object.values(charts).forEach((c) => c.dispose())
  Object.keys(charts).forEach((k) => delete charts[k])
}

function chartType(s: ReportSection): 'trend' | 'bar' | 'pie' | null {
  if (s.key === 'trend') return 'trend'
  if (s.key === 'merchantDetail') return 'bar'
  if (s.columns[0]?.key === 'dim' && s.rows.length) return 'pie'
  return null
}

const numericCols = (s: ReportSection) => s.columns.filter((c) => c.kind !== 'text')

function chartOption(s: ReportSection): Record<string, unknown> | null {
  const type = chartType(s)
  if (!type || !s.rows.length) return null
  if (type === 'pie') {
    const m = numericCols(s)[0]
    if (!m) return null
    return {
      tooltip: { trigger: 'item', formatter: (p: any) => `${p.name}<br/>${m.label} ${fmt(p.value, m.kind)}` },
      legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 11 } },
      series: [{
        type: 'pie', radius: ['38%', '62%'], center: ['50%', '44%'],
        label: { fontSize: 11, formatter: '{b} {c}' },
        data: s.rows.map((r) => ({ name: String(r.dim), value: Number(r[m.key] ?? 0) })),
      }],
      color: PALETTE,
    }
  }
  if (type === 'bar') {
    // 合计行只进表格，画进图里就成了一根压过所有商家的"总量柱"，读图会被它带偏
    const rows = s.rows.filter((r) => !rowClass({ row: r }).includes('row-total')).slice(0, 10)
    const moneys = numericCols(s).filter((c) => c.kind === 'money').slice(0, 3)
    if (!rows.length || !moneys.length) return null
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { top: 0, textStyle: { fontSize: 11 } },
      grid: { left: 96, right: 24, top: 34, bottom: 28 },
      xAxis: { type: 'value', axisLabel: { fontSize: 11 } },
      yAxis: { type: 'category', inverse: true, data: rows.map((r) => String(r.merchantName)),
        axisLabel: { fontSize: 11, width: 88, overflow: 'truncate' } },
      series: moneys.map((c) => ({
        name: c.label, type: 'bar', barMaxWidth: 14,
        data: rows.map((r) => Number(r[c.key] ?? 0)),
      })),
      color: PALETTE,
    }
  }
  const xs = s.rows.map((r) => String(r.bucket))
  const ints = numericCols(s).filter((c) => c.kind === 'int')
  const moneys = numericCols(s).filter((c) => c.kind === 'money')
  const series: any[] = ints.map((c) => ({
    name: c.label, type: 'bar', stack: null, barMaxWidth: 16, data: s.rows.map((r) => Number(r[c.key] ?? 0)),
  }))
  if (moneys.length) {
    series.push(...moneys.map((c) => ({
      name: c.label, type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6,
      data: s.rows.map((r) => Number(r[c.key] ?? 0)),
    })))
  }
  if (!series.length) return null
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 54, right: 62, top: 34, bottom: 28 },
    xAxis: { type: 'category', data: xs, axisLabel: { fontSize: 11, rotate: xs.length > 14 ? 45 : 0 } },
    yAxis: [
      { type: 'value', name: '数量', nameTextStyle: { fontSize: 11 }, axisLabel: { fontSize: 11 } },
      moneys.length
        ? { type: 'value', name: '金额', nameTextStyle: { fontSize: 11 },
            axisLabel: { fontSize: 11, formatter: (v: number) => `${v}` } }
        : { type: 'value', show: false },
    ],
    series,
    color: PALETTE,
  }
}

async function renderCharts() {
  await nextTick()
  disposeCharts()
  sections.value.forEach((s) => {
    const el = chartEls[s.key]
    const opt = chartOption(s)
    if (!el || !opt) return
    const c = echarts.init(el)
    c.setOption(opt)
    charts[s.key] = c
  })
}

// ---------------------------------------------------------------- 取数
function params(): ReportQuery {
  const q: ReportQuery = {
    start: dates.value?.[0] || undefined,
    end: dates.value?.[1] || undefined,
  }
  if (tab.value === 'custom') {
    q.dataset = dsKey.value
    q.dims = dims.value.join(',')
    q.metrics = metrics.value.join(',') || undefined
  } else {
    q.grain = grain.value
  }
  return q
}

const FETCHERS = {
  overview: statsOverviewApi,
  merchant: statsMerchantApi,
  airspace: statsAirspaceApi,
}

async function reload() {
  loading.value = true
  const t = tab.value
  try {
    if (t === 'custom') {
      if (!pool.value) pool.value = await reportFieldsApi()
      report.value = null
      custom.value = await reportCustomApi(params())
    } else {
      custom.value = null
      report.value = await FETCHERS[t](params())
    }
    await renderCharts()
  } catch {
    // 区间不合法等业务错误由请求层统一提示，这里只保留上一次的结果
  } finally {
    loading.value = false
  }
}

function onDataset() {
  // 维度/指标的键是按数据集白名单校验的，换数据集后旧选择必然不认识
  dims.value = ['day']
  metrics.value = []
  reload()
}

async function doExport() {
  const t = TABS.find((x) => x.key === tab.value)
  if (!t) return
  exporting.value = true
  try {
    const range = meta.value ? `-${meta.value.start}_${meta.value.end}` : ''
    await exportReportApi({ ...params(), report: t.report }, `${t.label}${range}.csv`)
    ElMessage.success('已导出，内容与页面同一份数据源')
  } finally {
    exporting.value = false
  }
}

const onResize = () => Object.values(charts).forEach((c) => c.resize())

onMounted(() => {
  reload()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  disposeCharts()
})
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.flex-gap { flex: 1; }
.body { min-height: 320px; }
.pool { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.pool .lbl { font-size: 12px; color: var(--ts-text-secondary); }
.tip { margin-bottom: 12px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 4px 10px;
  margin-bottom: 12px; padding: 12px 14px; }
.kpi { padding: 5px 0; }
.k { font-size: 12px; color: #667; }
.v { font-size: 20px; font-weight: 700; color: var(--ts-primary-dark); }
.v.money { color: #1f7a4d; }
.blk { margin-bottom: 12px; }
.blk-title { font-size: 13px; font-weight: 700; color: var(--ts-primary-dark); margin-bottom: 8px; }
.chart { width: 100%; height: 260px; }
.num { font-family: Consolas, monospace; }
.sub { font-size: 12px; color: var(--ts-text-secondary); }
.note { margin: 4px 2px 0; line-height: 1.7; }
:deep(.row-total) { background: #f2f5fb; font-weight: 700; }
</style>
