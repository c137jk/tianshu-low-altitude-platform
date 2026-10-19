<template>
  <div>
    <div class="welcome ts-card" style="display:flex;justify-content:space-between;align-items:center">
      <div>
        <h2>你好，{{ userStore.userInfo?.realName || userStore.userInfo?.username }}</h2>
        <p>天枢低空运营 · 运行驾驶舱</p>
      </div>
      <el-button v-if="userStore.hasPerm('demo:run')" type="primary" size="large"
        @click="$router.push('/admin/demo')">一键演示全链路</el-button>
    </div>
    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="6" v-for="c in cards" :key="c.label">
        <div class="stat ts-card">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-value num">{{ c.value }}</div>
        </div>
      </el-col>
    </el-row>
    <el-row :gutter="16" style="margin-top: 16px">
      <!-- 趋势线读 /stats/overview（report:view）：以前这段是前端写死的假数据，
           驾驶舱和报表中心各画一条线，谁也不认谁。没报表权限的岗位（如机长）不画这块。 -->
      <el-col v-if="canReport" :span="14">
        <div ref="trendEl" class="chart ts-card" />
      </el-col>
      <el-col :span="canReport ? 10 : 24">
        <div ref="pieEl" class="chart ts-card" />
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useUserStore } from '@/stores/user'
import request from '@/utils/request'
import { statsOverviewApi } from '@/api/stats'
import { ymd } from '@/utils/date'
import * as echarts from 'echarts'
const userStore = useUserStore()
const canReport = computed(() => userStore.hasPerm('report:view'))
const cards = ref([
  { label: '今日起降', value: '--' },
  { label: '在飞架次', value: '--' },
  { label: '机队告警', value: '--' },
  { label: '机队总数', value: '--' },
])
const trendEl = ref<HTMLElement | null>(null)
const pieEl = ref<HTMLElement | null>(null)
let tChart: any = null, pChart: any = null

async function loadTrend() {
  if (!trendEl.value) return
  const end = new Date()
  const start = new Date(end.getTime() - 6 * 86400000)
  const o = await statsOverviewApi({ start: ymd(start), end: ymd(end), grain: 'day' })
  const rows = o.sections.find((s) => s.key === 'trend')?.rows || []
  tChart = echarts.init(trendEl.value)
  tChart.setOption({
    title: {
      text: '近 7 日订单量与实付金额', left: 'center', textStyle: { fontSize: 14 },
      subtext: `${o.meta.start} ~ ${o.meta.end} · 与统计报表同一口径`,
      subtextStyle: { fontSize: 11, color: '#8a93a6' },
    },
    tooltip: { trigger: 'axis' },
    legend: { top: 54, textStyle: { fontSize: 11 } },
    grid: { left: 52, right: 66, top: 84, bottom: 28 },
    xAxis: { type: 'category', data: rows.map((r) => String(r.bucket).slice(5)) },
    yAxis: [
      { type: 'value', name: '单量', minInterval: 1 },
      { type: 'value', name: '金额' },
    ],
    series: [
      { name: '订单量', type: 'bar', barMaxWidth: 18, itemStyle: { color: '#23406E' },
        data: rows.map((r) => Number(r.orderCount || 0)) },
      { name: '完成量', type: 'bar', barMaxWidth: 18, itemStyle: { color: '#37d67a' },
        data: rows.map((r) => Number(r.doneCount || 0)) },
      { name: '实付金额', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6,
        itemStyle: { color: '#c8a45c' }, data: rows.map((r) => Number(r.payAmount || 0)) },
    ],
  })
}

onMounted(async () => {
  try {
    const d: any = await request('/monitor/screen')
    const low = (d.fleet || []).filter((a: any) => a.battery < 50).length
    cards.value = [
      { label: '今日起降', value: `${d.today.takeoffs}/${d.today.landings}` },
      { label: '在飞架次', value: d.flying.length },
      { label: '机队告警', value: low },
      { label: '机队总数', value: d.fleet.length },
    ]
    pChart = echarts.init(pieEl.value!)
    pChart.setOption({
      title: { text: '机队状态', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: {},
      series: [{
        type: 'pie', radius: ['40%', '65%'],
        data: [
          { value: d.fleet.filter((a: any) => a.status === 1).length, name: '空闲', itemStyle: { color: '#37d67a' } },
          { value: d.fleet.filter((a: any) => a.status === 2).length, name: '执行中', itemStyle: { color: '#c8a45c' } },
          { value: d.fleet.filter((a: any) => a.status !== 1 && a.status !== 2).length, name: '维修/其他', itemStyle: { color: '#8a93a6' } },
        ],
      }],
    })
    if (canReport.value) await loadTrend()
  } catch { /* 态势接口取不到就保持占位，不让驾驶舱整页报错 */ }
})
onBeforeUnmount(() => { tChart?.dispose(); pChart?.dispose() })
</script>

<style lang="scss" scoped>
.welcome h2 { margin: 0 0 8px; color: var(--ts-primary-dark); }
.welcome p { margin: 0; color: var(--ts-text-regular); font-size: 13px; }
.stat { text-align: center; padding: 22px 0; }
.stat-label { color: var(--ts-text-secondary); font-size: 13px; }
.stat-value { font-size: 30px; color: var(--ts-primary); margin-top: 8px; }
.chart { height: 320px; padding: 8px; }
</style>
