<template>
  <div>
    <el-tabs v-model="tab">
      <!-- 大屏内嵌：同源 iframe 会带上 localStorage 里的 token，管控台进来即全量视图 -->
      <el-tab-pane label="实时监控" name="realtime">
        <div class="screen-box">
          <iframe v-if="tab === 'realtime'" ref="screenRef" src="/screen" class="screen" />
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="can('monitor:track')" label="轨迹回放" name="track" lazy>
        <Track />
      </el-tab-pane>

      <el-tab-pane v-if="can('monitor:realtime')" label="运行告警" name="alerts" lazy>
        <Alerts />
      </el-tab-pane>

      <!-- 应急处置（monitor:emergency）：仿真注入失联/闯禁飞/低电量后在这里结案 -->
      <el-tab-pane v-if="can('monitor:emergency')" label="应急处置" name="emergency" lazy>
        <div class="bar">
          <span class="muted">事件来自真实链路（失联、闯入禁飞区、低电量、恶劣天气），落地后自动解除。</span>
          <div class="spacer"></div>
          <el-button size="small" @click="loadEmergencies">刷新</el-button>
        </div>
        <el-table :data="emergencies" stripe v-loading="emLoading" size="small">
          <el-table-column prop="event_no" label="事件编号" width="180" />
          <el-table-column label="类型" width="110">
            <template #default="{ row }">{{ EM_TYPE[row.event_type] || '其他' }}</template>
          </el-table-column>
          <el-table-column label="等级" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.level === 3 ? 'danger' : row.level === 2 ? 'warning' : 'info'">
                {{ EM_LEVEL[row.level] || '一般' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="240" />
          <el-table-column prop="started_at" label="发生时间" width="160" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 3 ? 'success' : 'danger'">
                {{ EM_STATUS[row.status] || '发生中' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button v-if="row.status !== 3" link type="primary" size="small"
                @click="openEm(row)">处置</el-button>
              <span v-else class="muted">{{ row.handle_measure || '已解除' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="can('monitor:device')" label="设备在线" name="devices" lazy>
        <Devices />
      </el-tab-pane>

      <!-- 违规处置（monitor:violation）：与「空域合规 → 违规处理」是同一块业务，
           组件复用以免两处台账口径不一致（处罚、通知书、监管上报都在同一份实现里）。 -->
      <el-tab-pane v-if="can('monitor:violation')" label="违规处置" name="violation" lazy>
        <Violations />
      </el-tab-pane>

      <el-tab-pane v-if="can('report:view')" label="监管报告" name="report" lazy>
        <Report />
      </el-tab-pane>
    </el-tabs>

    <!-- 应急处置 -->
    <el-dialog v-model="emShow" title="应急处置" width="460px">
      <p class="tip">事件「{{ cur?.event_no }} · {{ EM_TYPE[cur?.event_type] }}」处置措施：</p>
      <el-input v-model="emForm.measure" type="textarea" :rows="3"
        placeholder="如：已联系机长返航、已切换备降场站…" />
      <el-radio-group v-model="emForm.status" style="margin-top:10px">
        <el-radio :value="2">处置中</el-radio>
        <el-radio :value="3">已解除</el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="emShow = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="saveEm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onActivated, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import Track from './Track.vue'
import Alerts from './Alerts.vue'
import Devices from './Devices.vue'
import Report from './Report.vue'
import Violations from '../airspace/Violations.vue'
import request from '@/utils/request'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const can = (c: string) => userStore.hasPerm(c)

const EM_TYPE: Record<number, string> = { 1: '链路失联', 2: '闯入禁飞区', 3: '低电量', 4: '恶劣天气', 5: '其他' }
const EM_LEVEL: Record<number, string> = { 1: '一般', 2: '较大', 3: '重大' }
const EM_STATUS: Record<number, string> = { 1: '发生中', 2: '处置中', 3: '已解除' }

const tab = ref('realtime')
const screenRef = ref<HTMLIFrameElement | null>(null)
const busy = ref(false)

const emergencies = ref<any[]>([])
const emLoading = ref(false)

// 页签第一次打开才拉数据：没有对应权限的账号不会打这些请求（也就不会弹 403）
const loaded = reactive<Record<string, boolean>>({})
watch(tab, (v) => {
  if (v === 'emergency' && !loaded.emergency) { loaded.emergency = true; loadEmergencies() }
})
onMounted(() => {
  if (tab.value === 'emergency') { loaded.emergency = true; loadEmergencies() }
})

async function loadEmergencies() {
  emLoading.value = true
  try { emergencies.value = await request('/aux/emergencies') } finally { emLoading.value = false }
}

const cur = ref<any>(null)
const emShow = ref(false)
const emForm = reactive<any>({ measure: '', status: 2 })
function openEm(row: any) {
  cur.value = row
  Object.assign(emForm, { measure: row.handle_measure || '', status: row.status === 1 ? 2 : row.status })
  emShow.value = true
}
async function saveEm() {
  if (!emForm.measure) return ElMessage.warning('请填写处置措施')
  busy.value = true
  try {
    await request(`/aux/emergencies/${cur.value.id}`, { method: 'PUT', data: { ...emForm } })
    ElMessage.success('处置结果已记录')
    emShow.value = false
    loadEmergencies()
  } finally {
    busy.value = false
  }
}

// KeepAlive 场景：切走时（新页面挂载后）再释放大屏 iframe，
// 避免高德地图文档同步卸载堵住路由切换；切回来时重新加载
onActivated(() => {
  const f = screenRef.value
  if (f && f.src.includes('about:blank')) f.src = '/screen'
})
onDeactivated(() => {
  setTimeout(() => { if (screenRef.value) screenRef.value.src = 'about:blank' }, 60)
})
</script>

<style scoped>
.screen-box { height: calc(100vh - 170px); border: 1px solid #e2e5ee; border-radius: 4px; overflow: hidden; }
.screen { width: 100%; height: 100%; border: 0; }
.bar { display: flex; align-items: center; margin-bottom: 10px; }
.spacer { flex: 1; }
.muted { color: #909399; font-size: 12px; }
.tip { margin: 0 0 10px; font-size: 13px; color: #606266; }
</style>
