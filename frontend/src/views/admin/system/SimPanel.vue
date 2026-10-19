<template>
  <div v-loading="loading">
    <el-alert type="info" :closable="false" style="margin-bottom:12px"
      title="仿真器按 1 秒一帧推进在飞计划，倍速取自系统参数 sim_speed_factor（默认 60×，即一分钟演示完一小时航程）。场景注入会真实写入轨迹/违规/应急台账，仅平台运维与管理员可用。" />

    <div class="cards">
      <div class="card"><label>仿真线程</label><b :class="st.running && !st.paused ? 'ok' : 'warn'">
        {{ st.running ? (st.paused ? '已暂停' : '运行中') : '未启动' }}</b></div>
      <div class="card"><label>帧间隔</label><b>{{ st.tickSec }}s</b></div>
      <div class="card"><label>当前倍速</label><b>{{ st.speedFactor }}×</b></div>
      <div class="card"><label>累计 tick</label><b>{{ st.tickNo }}</b></div>
      <div class="card"><label>在飞架次</label><b>{{ (st.flying || []).length }}</b></div>
    </div>

    <div class="ops">
      <span class="lbl">倍速</span>
      <el-input-number v-model="speed" :min="1" :max="3600" :step="10" size="small" />
      <el-button size="small" :loading="busy" @click="applySpeed">应用</el-button>
      <el-divider direction="vertical" />
      <el-button size="small" :type="st.paused ? 'success' : 'warning'" :loading="busy" @click="togglePause">
        {{ st.paused ? '继续仿真' : '暂停仿真' }}
      </el-button>
      <el-divider direction="vertical" />
      <span class="lbl">场景</span>
      <el-select v-model="scenario" size="small" style="width:150px">
        <el-option v-for="s in scenarios" :key="s.key" :label="s.name" :value="s.key" />
      </el-select>
      <span class="lbl">目标</span>
      <el-select v-model="target" size="small" style="width:190px">
        <el-option label="全部在飞计划" value="" />
        <el-option v-for="f in st.flying" :key="f.planId" :label="`${f.planNo} · ${f.taskName}`"
          :value="String(f.planId)" />
      </el-select>
      <el-button size="small" type="primary" :loading="busy" @click="inject">注入</el-button>
      <div class="spacer"></div>
      <el-button size="small" text @click="load">刷新</el-button>
    </div>

    <el-table :data="st.flying || []" stripe size="small" style="margin-top:12px">
      <el-table-column prop="planNo" label="计划号" width="180" />
      <el-table-column prop="taskName" label="任务" min-width="140" />
      <el-table-column label="进度" width="180">
        <template #default="{ row }">
          <el-progress :percentage="Math.round((row.progress || 0) * 100)" :stroke-width="10" />
        </template>
      </el-table-column>
      <el-table-column label="当前场景" width="130">
        <template #default="{ row }">
          <el-tag size="small" :type="row.scenario === 'normal' || !row.scenario ? 'success' : 'danger'">
            {{ row.scenarioName }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="frames" label="已落帧" width="90" align="center" />
      <el-table-column label="操作" width="150">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="quick(row, 'normal')">恢复正常</el-button>
          <el-button link type="danger" size="small" @click="quick(row, 'no_fly')">闯禁飞</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <span class="muted">当前没有在飞计划。可先去「一键演示」跑一条链路，起飞后仿真器会自动接管。</span>
      </template>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'

const st = ref<any>({ flying: [] })
const scenarios = ref<any[]>([])
const scenario = ref('deviate')
const target = ref('')
const speed = ref(60)
const loading = ref(false)
const busy = ref(false)
let timer: number | null = null

async function load() {
  loading.value = !st.value.flying
  try {
    st.value = await request('/simulator/status')
    speed.value = st.value.speedFactor
  } finally {
    loading.value = false
  }
}

async function applySpeed() {
  busy.value = true
  try {
    const d: any = await request('/simulator/speed', { method: 'PUT', data: { speedFactor: speed.value } })
    ElMessage.success(`倍速已设为 ${d.speedFactor}×，立即生效`)
    load()
  } finally {
    busy.value = false
  }
}

async function togglePause() {
  busy.value = true
  try {
    await request('/simulator/pause', { method: 'POST', data: { paused: !st.value.paused } })
    load()
  } finally {
    busy.value = false
  }
}

async function doInject(sc: string, planId: string) {
  busy.value = true
  try {
    const d: any = await request('/simulator/inject', {
      method: 'POST', data: { scenario: sc, planId: planId || undefined },
    })
    ElMessage.success(d?.warning || `场景已注入：${sc}`)
    load()
  } finally {
    busy.value = false
  }
}
function inject() { doInject(scenario.value, target.value) }
function quick(row: any, sc: string) { doInject(sc, String(row.planId)) }

onMounted(async () => {
  try { scenarios.value = await request('/simulator/scenarios') } catch { scenarios.value = [] }
  await load()
  timer = window.setInterval(() => { if (!busy.value) load() }, 3000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
.card { border: 1px solid #ebeef5; border-radius: 6px; padding: 10px 12px; text-align: center; }
.card label { display: block; font-size: 12px; color: #909399; margin-bottom: 4px; }
.card b { font-size: 20px; font-family: Consolas, monospace; }
.ok { color: #37d67a; } .warn { color: #e6a23c; }
.ops { display: flex; align-items: center; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.lbl { font-size: 13px; color: #606266; }
.spacer { flex: 1; }
.muted { color: #909399; font-size: 13px; }
</style>
