<template>
  <el-card shadow="never">
    <el-tabs v-if="tabs.length" v-model="tab">
      <!-- 账号维护（system:user）-->
      <el-tab-pane v-if="can('system:user')" label="用户管理" name="users">
        <UsersPanel v-if="opened.users" />
      </el-tab-pane>

      <!-- 角色与权限维护（M-83，system:role）-->
      <el-tab-pane v-if="can('system:role')" label="角色与权限" name="roles">
        <RolesPanel v-if="opened.roles" />
      </el-tab-pane>

      <!-- 商家入驻审核（M-82，merchant:audit）-->
      <el-tab-pane v-if="can('merchant:audit')" label="商家入驻审核" name="merchants">
        <MerchantsPanel v-if="opened.merchants" />
      </el-tab-pane>

      <!-- 参数配置（system:config）-->
      <el-tab-pane v-if="can('system:config')" label="参数配置" name="config">
        <div v-loading="cLoading">
          <el-alert type="info" :closable="false" style="margin-bottom:10px"
            title="改完立即生效：读取侧有 10 秒 TTL 缓存，保存时后端会主动失效对应键。数字类参数按 value_type 校验。" />
          <el-table :data="configs" stripe size="small">
            <el-table-column prop="config_group" label="分组" width="110" />
            <el-table-column prop="config_key" label="参数键" width="240">
              <template #default="{ row }"><span class="mono">{{ row.config_key }}</span></template>
            </el-table-column>
            <el-table-column label="参数值" width="200">
              <template #default="{ row }">
                <el-input v-model="row.config_value" size="small" />
              </template>
            </el-table-column>
            <el-table-column prop="value_type" label="类型" width="80" />
            <el-table-column prop="remark" label="说明" min-width="200" />
            <el-table-column label="操作" width="80">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="saveCfg(row)">保存</el-button>
              </template>
            </el-table-column>
          </el-table>

          <!-- 定时任务（B6-2）：阈值就在这张表里，任务状态放在它下面一屏看 -->
          <div class="sched">
            <div class="sched-head">
              <span class="sched-title">定时任务</span>
              <el-tag size="small" :type="sched?.running ? 'success' : 'info'">
                {{ sched?.running ? '调度器运行中' : (sched?.disabled ? '后台线程已停用' : '调度器未运行') }}
              </el-tag>
              <el-button link type="primary" size="small" :loading="sLoading" @click="loadScheduler">
                刷新
              </el-button>
              <span class="sched-hint">阈值改完 10 秒内生效，任务按下一轮周期读新值</span>
            </div>
            <el-table :data="sched?.jobs || []" stripe size="small">
              <el-table-column prop="name" label="任务" width="180" />
              <el-table-column prop="id" label="标识" width="170">
                <template #default="{ row }"><span class="mono">{{ row.id }}</span></template>
              </el-table-column>
              <el-table-column prop="trigger" label="触发周期" min-width="190" />
              <el-table-column label="下次执行" width="180">
                <template #default="{ row }">{{ row.nextRunAt || '—' }}</template>
              </el-table-column>
            </el-table>
            <p class="sched-note">
              超时未接单催办与场站预约提醒按分钟轮询；资质到期扫描、轨迹月分区预建、到期券下架每天凌晨各跑一次。
              同一件业务事件只提醒一次，任务补跑不会重复轰炸。
              轨迹分区<strong>默认只预建不删除</strong>：把
              <span class="mono">track_partition_drop_enabled</span> 置 1 才会按
              <span class="mono">track_online_months</span> 整月 DROP（那是删真实轨迹数据）。
            </p>
          </div>
        </div>
      </el-tab-pane>

      <!-- 日志审计（system:log）-->
      <el-tab-pane v-if="can('system:log')" label="日志审计" name="logs">
        <div style="margin-bottom:12px">
          <el-input v-model="logKw" placeholder="按账号/模块/URL 搜索" style="width:280px"
            clearable @keyup.enter="loadLogs" />
          <el-button size="small" @click="loadLogs">搜索</el-button>
        </div>
        <el-table :data="logs" stripe size="small" v-loading="lLoading" height="520">
          <el-table-column prop="created_at" label="时间" width="170" />
          <el-table-column prop="username_snap" label="操作人" width="120" />
          <el-table-column prop="module" label="模块" width="120" />
          <el-table-column prop="method" label="方法" width="80" />
          <el-table-column prop="request_url" label="请求URL" min-width="240" />
          <el-table-column prop="ip" label="IP" width="130" />
          <el-table-column prop="status" label="状态码" width="80" />
        </el-table>
      </el-tab-pane>

      <!-- 公告 / 帮助（system:article）-->
      <el-tab-pane v-if="can('system:article')" label="公告与帮助" name="articles">
        <div style="margin-bottom:12px;text-align:right">
          <el-button type="primary" size="small" @click="openArt()">发布内容</el-button>
        </div>
        <el-table :data="articles" stripe size="small" v-loading="aLoading">
          <el-table-column prop="category" label="分类" width="120" />
          <el-table-column prop="title" label="标题" min-width="220" />
          <el-table-column prop="view_count" label="浏览" width="80" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 1 ? 'success' : 'info'">
                {{ row.status === 1 ? '已发布' : '已下架' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="170">
            <template #default="{ row }">
              <el-button link size="small" @click="openArt(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="delArt(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 仿真控制（demo:run）：5 类场景注入 + 倍速 -->
      <el-tab-pane v-if="can('demo:run')" label="仿真控制" name="sim">
        <SimPanel v-if="opened.sim" />
      </el-tab-pane>
    </el-tabs>

    <el-empty v-else description="当前账号没有任何系统管理权限（system:*），请联系平台管理员开通" />

    <!-- 文章编辑 -->
    <el-dialog v-model="artShow" :title="artForm.id ? '编辑内容' : '发布内容'" width="560px">
      <el-form :model="artForm" label-width="72px">
        <el-form-item label="分类">
          <el-select v-model="artForm.category" style="width:100%">
            <el-option label="低空公告" value="低空公告" />
            <el-option label="使用指南" value="使用指南" />
            <el-option label="常见问题" value="常见问题" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="artForm.title" />
        </el-form-item>
        <el-form-item label="正文">
          <el-input v-model="artForm.content" type="textarea" :rows="6" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="artForm.status">
            <el-radio :value="1">发布</el-radio>
            <el-radio :value="2">下架</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="artShow = false">取消</el-button>
        <el-button type="primary" @click="saveArt">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
import { useUserStore } from '@/stores/user'
import UsersPanel from './UsersPanel.vue'
import RolesPanel from './RolesPanel.vue'
import MerchantsPanel from './MerchantsPanel.vue'
import SimPanel from './SimPanel.vue'

const userStore = useUserStore()
const route = useRoute()
const can = (code: string) => userStore.hasPerm(code)

// 只渲染当前账号看得见的页签；默认停在第一个有权的页签（支持 ?tab=roles 直达）
const ALL_TABS = [
  { name: 'users', perm: 'system:user' },
  { name: 'roles', perm: 'system:role' },
  { name: 'merchants', perm: 'merchant:audit' },
  { name: 'config', perm: 'system:config' },
  { name: 'logs', perm: 'system:log' },
  { name: 'articles', perm: 'system:article' },
  { name: 'sim', perm: 'demo:run' },
]
const tabs = computed(() => ALL_TABS.filter((t) => can(t.perm)).map((t) => t.name))
const tab = ref<string>('')

// 页签内容首次激活才请求，避免一进系统中心就打一串 403
const opened = reactive<Record<string, boolean>>({})
watch(tab, (v) => { opened[v] = true; lazyLoad(v) }, { immediate: true })

const cLoading = ref(false)
const sLoading = ref(false)
const lLoading = ref(false)
const aLoading = ref(false)
const configs = ref<any[]>([])
const logs = ref<any[]>([])
const logKw = ref('')
const articles = ref<any[]>([])

interface SchedJob { id: string; name: string; trigger: string; nextRunAt: string | null }
interface SchedState { running: boolean; disabled: boolean; jobs: SchedJob[] }
const sched = ref<SchedState | null>(null)

async function loadConfigs() {
  cLoading.value = true
  try { configs.value = await request('/aux/configs') } finally { cLoading.value = false }
}
async function loadScheduler() {
  sLoading.value = true
  try { sched.value = await request('/aux/scheduler/jobs') } finally { sLoading.value = false }
}
async function saveCfg(row: any) {
  await request(`/aux/configs/${row.id}`, { method: 'PUT', data: { value: row.config_value } })
  ElMessage.success(`${row.config_key} 已保存`)
  loadConfigs()
}
async function loadLogs() {
  lLoading.value = true
  try {
    logs.value = await request('/aux/logs', {
      params: { keyword: logKw.value || undefined, pageSize: 300 },
    })
  } finally { lLoading.value = false }
}
async function loadArticles() {
  aLoading.value = true
  try { articles.value = await request('/aux/articles') } finally { aLoading.value = false }
}
function lazyLoad(name: string) {
  if (name === 'config' && !configs.value.length) loadConfigs()
  if (name === 'config' && !sched.value) loadScheduler()
  if (name === 'logs' && !logs.value.length) loadLogs()
  if (name === 'articles' && !articles.value.length) loadArticles()
}

const artShow = ref(false)
const artForm = reactive<any>({ id: null, category: '低空公告', title: '', content: '', status: 1 })
function openArt(row?: any) {
  Object.assign(artForm, row
    ? { ...row }
    : { id: null, category: '低空公告', title: '', content: '', status: 1 })
  artShow.value = true
}
async function saveArt() {
  if (!artForm.title) return ElMessage.warning('请填写标题')
  if (artForm.id) await request(`/aux/articles/${artForm.id}`, { method: 'PUT', data: { ...artForm } })
  else await request('/aux/articles', { method: 'POST', data: { ...artForm } })
  ElMessage.success('已保存')
  artShow.value = false
  loadArticles()
}
async function delArt(row: any) {
  await ElMessageBox.confirm('确认删除该内容？', '提示', { type: 'warning' })
  await request(`/aux/articles/${row.id}`, { method: 'DELETE' })
  loadArticles()
}

onMounted(() => {
  const want = String(route.query.tab || '')
  tab.value = tabs.value.includes(want) ? want : tabs.value[0] || ''
})
</script>

<style scoped>
.mono { font-family: Consolas, monospace; }
.sched { margin-top: 20px; }
.sched-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.sched-title { font-weight: 600; }
.sched-hint { font-size: 12px; color: var(--el-text-color-secondary); }
.sched-note {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}
</style>
