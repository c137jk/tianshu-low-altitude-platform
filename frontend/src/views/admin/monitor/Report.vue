<template>
  <div v-loading="loading" class="report">
    <div class="rp-head">
      <div>
        <b>合规运行监管报告</b>
        <div class="muted">统计口径来自 violation_record / emergency_event / flight_plan 等运行库表，
          取当前全量快照，用于向属地民航监管报送。金额与「统计报表中心」同一套口径（实付且未作废）。</div>
      </div>
      <div class="spacer" />
      <span class="muted">生成时间 {{ genAt }}</span>
      <el-button size="small" @click="load">刷新</el-button>
    </div>

    <div class="rate">
      <div class="rate-item">
        <div class="label">订单完成率</div>
        <div class="value num">{{ d.completionRate ?? 0 }}<em>%</em></div>
        <div class="muted">已完成 {{ d.orderDone ?? 0 }} / 订单 {{ d.orderTotal ?? 0 }}</div>
      </div>
      <div class="rate-item">
        <div class="label">违规发生率</div>
        <div class="value num" :class="{ bad: (d.violationRate ?? 0) > 5 }">{{ d.violationRate ?? 0 }}<em>%</em></div>
        <div class="muted">违规 {{ d.violationTotal ?? 0 }} / 飞行计划 {{ d.planTotal ?? 0 }}</div>
      </div>
      <div class="rate-item">
        <div class="label">在办事项</div>
        <div class="value num">{{ (d.violationOpen ?? 0) + (d.emergencyOpen ?? 0) }}</div>
        <div class="muted">违规待处理 {{ d.violationOpen ?? 0 }} · 应急未解除 {{ d.emergencyOpen ?? 0 }}</div>
      </div>
    </div>

    <el-row :gutter="10">
      <el-col v-for="g in groups" :key="g.title" :span="12" :lg="6" class="grp-col">
        <div class="grp">
          <div class="grp-title">{{ g.title }}</div>
          <div v-for="it in g.items" :key="it.label" class="line">
            <span class="muted">{{ it.label }}</span>
            <b class="num">{{ it.value }}</b>
          </div>
        </div>
      </el-col>
    </el-row>

    <p class="muted tip">
      明细台账（违规记录、处罚与《违规处理通知书》、应急事件处置时间线）见「违规处置」与「应急处置」页签，
      「违规处置」页签右上角可直接导出监管上报清单（CSV）。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import request from '@/utils/request'
import { ymdHms } from '@/utils/date'

const d = ref<any>({})
const loading = ref(true)
const genAt = ref('')

const money = (v: any) => `¥${Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
// 报送件上的时间戳统一 'YYYY-MM-DD HH:mm:ss'：本地时间，且和台账里其它时间列同一种写法，
// 监管侧核对不会看成两套口径。
const stamp = () => ymdHms(new Date())
const minutes = (v: any) => {
  const m = Math.round(Number(v || 0))
  return m >= 60 ? `${Math.floor(m / 60)} 小时 ${m % 60} 分` : `${m} 分钟`
}

const groups = computed(() => [
  {
    title: '订单与任务',
    items: [
      { label: '订单总量', value: d.value.orderTotal ?? 0 },
      { label: '已完成', value: d.value.orderDone ?? 0 },
      { label: '履约中', value: d.value.orderFlying ?? 0 },
      { label: '飞行计划', value: d.value.planTotal ?? 0 },
      { label: '累计飞行', value: minutes(d.value.flyMinutes) },
    ],
  },
  {
    title: '空域合规',
    items: [
      { label: '违规记录', value: d.value.violationTotal ?? 0 },
      { label: '待处理/处理中', value: d.value.violationOpen ?? 0 },
      { label: '已处罚', value: d.value.violationPunished ?? 0 },
      { label: '应急事件未解除', value: d.value.emergencyOpen ?? 0 },
      { label: '在册空域', value: d.value.airspaceCount ?? 0 },
    ],
  },
  {
    title: '资源与设备',
    items: [
      { label: '飞行器总数', value: d.value.aircraftTotal ?? 0 },
      { label: '执行中', value: d.value.aircraftFlying ?? 0 },
      { label: '起降场站', value: d.value.vertiportTotal ?? 0 },
    ],
  },
  {
    title: '经营口径',
    items: [
      // 名字跟着后端统计口径改：这两个数就是"已支付且未作废"的实付额和按费率计提的抽成，
      // 叫"已支付营收/平台服务费"容易被读成含退款、含开票服务费，和报表中心对不上
      { label: '实付营收（未作废）', value: money(d.value.revenue) },
      { label: '平台抽成', value: money(d.value.platformFee) },
    ],
  },
])

async function load() {
  loading.value = true
  try {
    d.value = await request('/aux/report')
    genAt.value = stamp()
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.report { padding-top: 4px; }
.rp-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.rp-head b { font-size: 15px; }
.spacer { flex: 1; }
.muted { color: #909399; font-size: 12px; }
.rate { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.rate-item { flex: 1 1 220px; border: 1px solid #e2e5ee; border-radius: 4px; padding: 12px 14px; background: #fbfcfe; }
.rate-item .label { font-size: 12px; color: #66708a; }
.rate-item .value { font-size: 26px; color: var(--ts-primary); line-height: 1.3; }
.rate-item .value em { font-size: 14px; font-style: normal; margin-left: 2px }
.rate-item .value.bad { color: #c0392b; }
.grp-col { margin-bottom: 10px; }
.grp { border: 1px solid #e2e5ee; border-radius: 4px; padding: 10px 12px; height: 100%; }
.grp-title { font-size: 13px; font-weight: 700; color: var(--ts-primary-dark); margin-bottom: 8px; }
/* 中文标签按字换行很难看，宁可撑开列宽也不折行 */
.line { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; padding: 3px 0; font-size: 13px; }
.line span, .line b { white-space: nowrap; }
.tip { margin: 14px 0 0; line-height: 1.6; }
</style>
