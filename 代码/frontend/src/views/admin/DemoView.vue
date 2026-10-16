<template>
  <div class="demo">
    <el-card>
      <template #header>
        <div class="hd">
          <b>一键演示</b>
          <div class="hd-right">
            <span class="lbl">仿真倍速</span>
            <el-select v-model="speed" size="small" style="width: 168px" :disabled="loading">
              <el-option v-for="s in SPEEDS" :key="s.value" :value="s.value" :label="s.label" />
            </el-select>
            <el-button type="primary" size="small" :loading="loading" @click="run">
              {{ loading ? '演示进行中...' : '开始一键演示' }}
            </el-button>
            <!-- 不置灰：大屏要在点演示之前就开着，否则倍速一快，飞机从起飞到落地
                 只有几秒，切过去已经落地了，看起来就是"大屏上没有送货信息"。 -->
            <el-button size="small" @click="openScreen">打开监控大屏</el-button>
          </div>
        </div>
      </template>

      <el-alert type="info" :closable="false" style="margin-bottom: 14px"
        title="点击后系统自动完成：下单支付 → 智能推荐 → 调度方案 → 空管申报 → 审批 → 下发机长 → 起飞，之后由飞行仿真器按帧推进到送达。" />
      <el-alert type="success" :closable="false" style="margin-bottom: 14px"
        title="想看送货过程：先点右侧「打开监控大屏」把大屏开在另一个窗口/页签，再回来点「开始一键演示」——起飞后大屏的「在飞任务」会实时显示这单的进度、机长、高度与电量，下方实况面板同步推进。" />
      <el-alert type="warning" :closable="false" style="margin-bottom: 14px"
        :title="`倍速写的是系统参数 sim_speed_factor（和「系统管理 → 仿真控制」同一个旋钮），对所有在飞计划生效。当前 ${speed}×：${watchHint}`" />

      <!-- 在飞实况：不开这个面板的话，演示跑完只有一串流水，大屏上什么都看不到 -->
      <div v-if="planId" class="live ts-card">
        <div class="live-hd">
          <b>{{ taskName || planNo }}</b>
          <el-tag :type="done ? 'success' : 'primary'" size="small" effect="light">
            {{ done ? '已送达' : phase }}
          </el-tag>
        </div>
        <el-progress :percentage="pct" :stroke-width="14" :status="done ? 'success' : undefined" />
        <div class="live-grid">
          <div><label>计划号</label><b class="num">{{ planNo || '—' }}</b></div>
          <div><label>订单号</label><b class="num">{{ orderNo || '—' }}</b></div>
          <div><label>飞行器 / 机长</label><b>{{ aircraftNo }} / {{ pilot }}</b></div>
          <div><label>航程 / 航时</label><b class="num">{{ fmtDistance(distanceM) }} / {{ fmtDuration(durationSec) }}</b></div>
          <div><label>轨迹帧</label><b class="num">{{ frames }}</b></div>
          <div><label>注入场景</label><b>{{ scenarioName }}</b></div>
        </div>
        <p class="tip">
          {{ done
            ? '本次演示已送达：大屏「今日起飞/降落」计数已增加，轨迹可在「监控中心 → 轨迹回放」复现。'
            : '飞机正在航线上推进，切到监控大屏可看到它沿航线移动、右侧「在飞任务」卡片实时刷新。' }}
        </p>
      </div>

      <el-timeline v-if="steps.length" style="margin-top: 16px">
        <el-timeline-item v-for="(s, i) in steps" :key="i"
          :timestamp="s.detail" placement="top" color="#c8a45c">
          {{ s.step }}
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else-if="!planId" description="尚未开始" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'
import { fmtDistance, fmtDuration } from '@/constants/dict'

// 60× 是"跑回归"的倍速：10 分钟航程 10 秒飞完，人切到大屏时飞机已经落地了。
// 演示默认 10×，全程约 1 分钟，看得清起飞→巡航→进近→送达。
const SPEEDS = [
  { value: 1, label: '1×（真实时长）' },
  { value: 6, label: '6×（约 10 分钟航程 → 100 秒）' },
  { value: 10, label: '10×（推荐，看得清全程）' },
  { value: 30, label: '30×（快节奏）' },
  { value: 60, label: '60×（一闪而过，跑回归用）' },
]

const speed = ref(10)
const loading = ref(false)
const steps = ref<any[]>([])
const planId = ref<number | null>(null)
const planNo = ref('')
const orderNo = ref('')
const taskName = ref('')
const aircraftNo = ref('')
const pilot = ref('')
const distanceM = ref(0)
const durationSec = ref(0)
const frames = ref(0)
const scenarioName = ref('正常巡航')
const pct = ref(0)
const done = ref(false)
let timer: number | null = null

const watchHint = computed(() => {
  if (!durationSec.value) return '演示跑起来后会显示全程需要多少秒。'
  const sec = Math.max(1, Math.round(durationSec.value / speed.value))
  return `本次航时 ${fmtDuration(durationSec.value)}，大屏上约 ${sec} 秒飞完。`
})
const phase = computed(() => {
  const p = pct.value / 100
  if (p < 0.15) return '起飞爬升'
  if (p < 0.45) return '前往取货点'
  if (p < 0.8) return '前往送货点'
  return '进近降落'
})

function openScreen() {
  window.open('/screen', '_blank')
}

function stopPolling() {
  if (timer) { window.clearInterval(timer); timer = null }
}

/** 轮询仿真器：它就是大屏的同一份数据源，进度到 1 会被自动降落并从在飞列表里摘掉 */
function startPolling() {
  stopPolling()
  timer = window.setInterval(async () => {
    try {
      const st = await request('/simulator/status')
      const me = (st.flying || []).find((f: any) => f.planId === planId.value)
      if (!me) {
        if (pct.value > 0) { done.value = true; pct.value = 100 }
        stopPolling()
        return
      }
      pct.value = Math.round((me.progress || 0) * 100)
      frames.value = me.frames || 0
      scenarioName.value = me.scenarioName || '正常巡航'
    } catch {
      stopPolling()   // 没有 demo:run 之外的异常就别一直打
    }
  }, 1000)
}

async function run() {
  loading.value = true
  stopPolling()
  steps.value = []
  done.value = false
  pct.value = 0
  frames.value = 0
  try {
    // 先把倍速定下来，否则起飞后一秒就落地，大屏上什么都看不见
    await request('/simulator/speed', { method: 'PUT', data: { speedFactor: speed.value } })
    const d = await request('/demo/run', { method: 'POST' })
    steps.value = d.steps
    planId.value = d.planId
    planNo.value = d.planNo
    orderNo.value = d.orderNo
    aircraftNo.value = d.aircraftNo
    pilot.value = d.pilot
    distanceM.value = d.distanceM
    durationSec.value = d.durationSec
    taskName.value = `一键演示 ${d.takeoffStation} → ${d.landingStation}`
    if (d.finished) {
      done.value = true
      pct.value = 100
    } else {
      startPolling()
    }
    ElMessage.success(`已起飞，${watchHint.value}`)
  } finally {
    loading.value = false
  }
}

onBeforeUnmount(stopPolling)
</script>

<style lang="scss" scoped>
.demo { max-width: 900px; }
.hd { display: flex; align-items: center; justify-content: space-between; }
.hd-right { display: flex; align-items: center; gap: 8px; }
.lbl { font-size: 12px; color: var(--ts-text-secondary); }
.live { padding: 14px; background: var(--ts-bg); }
.live-hd { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.live-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 12px; }
.live-grid > div { background: #fff; border: 1px solid var(--ts-border); border-radius: 4px; padding: 8px 10px; }
.live-grid label { display: block; font-size: 11px; color: var(--ts-text-secondary); }
.live-grid b { font-size: 13px; color: var(--ts-primary-dark); }
.tip { margin: 12px 0 0; font-size: 12px; color: var(--ts-text-secondary); line-height: 1.6; }
.num { font-family: monospace; }
</style>
