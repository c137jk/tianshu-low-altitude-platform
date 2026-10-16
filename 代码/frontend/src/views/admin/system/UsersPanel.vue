<template>
  <div>
    <!-- 检索条：账号/姓名关键词 + 状态 + 岗位角色（对齐 GET /system/users 的查询参数）-->
    <div class="bar">
      <el-input v-model="q.keyword" placeholder="账号 / 姓名" clearable style="width:200px"
        @keyup.enter="search" @clear="search" />
      <el-select v-model="q.status" placeholder="状态" clearable style="width:120px" @change="search">
        <el-option label="正常" :value="1" />
        <el-option label="冻结" :value="2" />
      </el-select>
      <el-select v-model="q.roleCode" placeholder="岗位角色" clearable style="width:150px" @change="search">
        <el-option v-for="r in roleOptions" :key="r.roleCode" :label="r.roleName" :value="r.roleCode" />
      </el-select>
      <el-button @click="search">查询</el-button>
      <div class="spacer"></div>
      <el-button type="primary" @click="openCreate">新建子账号</el-button>
    </div>

    <el-table :data="rows" stripe v-loading="loading">
      <el-table-column prop="username" label="账号" width="130" />
      <el-table-column prop="realName" label="姓名" width="110" />
      <el-table-column label="岗位角色" width="170">
        <template #default="{ row }">
          <el-tag v-for="n in row.roleNames" :key="n" size="small" style="margin-right:4px">{{ n }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="phone" label="手机号" width="130">
        <template #default="{ row }"><span class="mono">{{ row.phone || '-' }}</span></template>
      </el-table-column>
      <el-table-column label="所属主体" width="130">
        <template #default="{ row }">{{ row.merchantName || typeName(row.userType) }}</template>
      </el-table-column>
      <el-table-column label="实名" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="REALTYPE[row.realnameStatus]?.t || 'info'">
            {{ REALTYPE[row.realnameStatus]?.n || '未提交' }}
          </el-tag>
        </template>
      </el-table-column>
      <!-- 状态口径：users.status 只有 1 正常 / 2 冻结 / 3 注销（早期 0/1 开关是 bug）-->
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <span v-if="locked(row)" class="muted">{{ row.statusName }}</span>
          <el-switch v-else :model-value="row.status" :active-value="1" :inactive-value="2"
            active-text="正常" inactive-text="冻结" inline-prompt @change="setStatus(row)" />
        </template>
      </el-table-column>
      <el-table-column label="最近登录" width="140">
        <template #default="{ row }"><span class="mono muted">{{ row.lastLoginAt || '从未登录' }}</span></template>
      </el-table-column>
      <el-table-column label="操作" min-width="190">
        <template #default="{ row }">
          <el-button link type="primary" size="small" :disabled="locked(row)" @click="openReset(row)">
            重置密码
          </el-button>
          <el-button v-if="canRole" link type="primary" size="small" :disabled="locked(row)"
            @click="openRoles(row)">分配角色</el-button>
          <el-button v-if="!locked(row)" link type="danger" size="small" @click="cancel(row)">注销</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination style="margin-top:12px;justify-content:flex-end" layout="total, sizes, prev, pager, next"
      :total="total" v-model:current-page="q.page" v-model:page-size="q.pageSize"
      :page-sizes="[10, 20, 50]" @change="load" />

    <!-- 新建子账号 -->
    <el-dialog v-model="createShow" title="新建子账号" width="480px">
      <el-form :model="form" label-width="88px">
        <el-form-item label="账号" required>
          <el-input v-model="form.username" placeholder="登录名，全站唯一" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="form.realName" placeholder="真实姓名，大屏与任务单会展示" />
        </el-form-item>
        <el-form-item label="岗位角色" required>
          <el-select v-model="form.roleCode" style="width:100%">
            <el-option v-for="r in roleOptions" :key="r.roleCode" :label="r.roleName" :value="r.roleCode" />
          </el-select>
        </el-form-item>
        <el-form-item label="手机号" required>
          <el-input v-model="form.phone" maxlength="11" placeholder="11 位手机号（加密存储，列表仅显示掩码）" />
        </el-form-item>
        <el-form-item label="初始密码">
          <el-input v-model="form.password" placeholder="留空即 Admin@123，至少 8 位" show-password />
        </el-form-item>
        <el-form-item v-if="isPlatform && form.roleCode !== 'customer'" label="所属商家">
          <el-input v-model="form.merchantId" placeholder="商家 ID，默认 1" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createShow = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="resetShow" title="重置密码" width="420px">
      <p class="tip">正在为「{{ cur?.realName || cur?.username }}」重置登录密码，重置后请通知本人尽快修改。</p>
      <el-input v-model="newPwd" placeholder="新密码，至少 8 位" show-password />
      <template #footer>
        <el-button @click="resetShow = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doReset">确认重置</el-button>
      </template>
    </el-dialog>

    <!-- 分配角色 -->
    <el-dialog v-model="rolesShow" title="分配角色" width="460px">
      <p class="tip">账号「{{ cur?.username }}」当前角色（可多选，保存后用户类型随岗位变化）：</p>
      <el-checkbox-group v-model="pickedRoleIds">
        <div v-for="r in allRoles" :key="r.id" class="role-line">
          <el-checkbox :value="r.id">
            {{ r.roleName }}<span class="muted mono">（{{ r.roleCode }}）</span>
          </el-checkbox>
        </div>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="rolesShow = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSetRoles">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const isPlatform = computed(() => [4, 5, 6].includes(userStore.userInfo?.userType ?? 0))
const canRole = computed(() => userStore.hasPerm('system:role'))

const REALTYPE: Record<number, { n: string; t: string }> = {
  0: { n: '未提交', t: 'info' }, 1: { n: '审核中', t: 'warning' },
  2: { n: '已认证', t: 'success' }, 3: { n: '已驳回', t: 'danger' },
}
const TYPE_NAME: Record<number, string> = {
  1: '个人客户', 2: '企业客户', 3: '商家员工', 4: '平台管理', 5: '空管监管', 6: '平台运维',
}
const typeName = (t: number) => TYPE_NAME[t] || '-'

const q = reactive<any>({ page: 1, pageSize: 20, keyword: '', status: undefined, roleCode: '' })
const rows = ref<any[]>([])
const total = ref(0)
const loading = ref(false)
const saving = ref(false)

async function load() {
  loading.value = true
  try {
    const d: any = await request('/system/users', {
      params: {
        page: q.page, pageSize: q.pageSize,
        keyword: q.keyword || undefined, status: q.status ?? undefined,
        roleCode: q.roleCode || undefined,
      },
    })
    rows.value = d.list
    total.value = d.total
  } finally {
    loading.value = false
  }
}
function search() { q.page = 1; load() }

/** 超管账号、自己、以及平台侧高权限账号（后端同样有门禁）前端先置灰，避免误点 */
function locked(row: any) {
  return row.id === 1 || row.id === userStore.userInfo?.id || row.status === 3
}

async function setStatus(row: any) {
  const next = row.status === 1 ? 2 : 1
  const action = next === 2 ? '冻结' : '启用'
  try {
    await ElMessageBox.confirm(
      `确认${action}账号「${row.realName || row.username}」？${next === 2 ? '冻结后该账号将无法登录。' : ''}`,
      `${action}确认`, { type: 'warning' })
  } catch { return }
  await request(`/system/users/${row.id}/status`, { method: 'PUT', data: { status: next } })
  ElMessage.success(`已${action}`)
  load()
}

async function cancel(row: any) {
  await ElMessageBox.confirm(
    `注销账号「${row.realName || row.username}」？注销后该账号从列表移除且不可恢复，历史业务数据保留。`,
    '注销确认', { type: 'error', confirmButtonText: '确认注销' })
  await request(`/system/users/${row.id}/status`, { method: 'PUT', data: { status: 3 } })
  ElMessage.success('已注销')
  load()
}

// ---- 岗位角色下拉（后端按登录者权限过滤，平台岗位只有授权账号能看到）----
const roleOptions = ref<any[]>([])
async function loadRoleOptions() {
  try { roleOptions.value = (await request('/system/role-options')).list } catch { roleOptions.value = [] }
}

// ---- 新建 ----
const createShow = ref(false)
const form = reactive<any>({
  username: '', realName: '', roleCode: 'dispatcher', phone: '', password: '', merchantId: '',
})
function openCreate() {
  Object.assign(form, {
    username: '', realName: '', roleCode: roleOptions.value[0]?.roleCode || 'dispatcher',
    phone: '', password: '', merchantId: '',
  })
  createShow.value = true
}
async function create() {
  if (!form.username) return ElMessage.warning('请填写账号')
  if (!form.phone || form.phone.length !== 11) return ElMessage.warning('请填写 11 位手机号')
  saving.value = true
  try {
    const d: any = await request('/system/users', { method: 'POST', data: { ...form } })
    await ElMessageBox.alert(
      `账号 ${d.username} 创建成功，初始密码：${d.initialPassword}（请转交本人后尽快修改）`,
      '创建成功', { type: 'success' })
    createShow.value = false
    load()
  } finally {
    saving.value = false
  }
}

// ---- 重置密码 ----
const resetShow = ref(false)
const newPwd = ref('')
const cur = ref<any>(null)
function openReset(row: any) { cur.value = row; newPwd.value = ''; resetShow.value = true }
async function doReset() {
  if (newPwd.value.length < 8) return ElMessage.warning('密码至少 8 位')
  saving.value = true
  try {
    await request(`/system/users/${cur.value.id}/password`,
      { method: 'PUT', data: { password: newPwd.value } })
    ElMessage.success('密码已重置')
    resetShow.value = false
  } finally {
    saving.value = false
  }
}

// ---- 分配角色 ----
const rolesShow = ref(false)
const allRoles = ref<any[]>([])
const pickedRoleIds = ref<number[]>([])
async function openRoles(row: any) {
  cur.value = row
  if (!allRoles.value.length) allRoles.value = (await request('/system/roles')).list
  pickedRoleIds.value = [...(row.roleIds || [])]
  rolesShow.value = true
}
async function doSetRoles() {
  if (!pickedRoleIds.value.length) return ElMessage.warning('至少选择一个角色')
  saving.value = true
  try {
    await request(`/system/users/${cur.value.id}/roles`,
      { method: 'PUT', data: { roleIds: pickedRoleIds.value } })
    ElMessage.success('角色已更新，该账号下次进入菜单即生效')
    rolesShow.value = false
    load()
  } finally {
    saving.value = false
  }
}

onMounted(() => { loadRoleOptions(); load() })
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.spacer { flex: 1; }
.mono { font-family: Consolas, monospace; }
.muted { color: #909399; font-size: 12px; }
.tip { margin: 0 0 10px; font-size: 13px; color: #606266; line-height: 1.7; }
.role-line { padding: 2px 0; }
</style>
