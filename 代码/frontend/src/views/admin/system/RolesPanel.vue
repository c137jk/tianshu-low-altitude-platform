<template>
  <div class="wrap">
    <!-- 左：角色清单 -->
    <div class="left">
      <div class="bar">
        <b>角色列表</b>
        <div class="spacer"></div>
        <el-button size="small" type="primary" @click="openCreate">新建</el-button>
      </div>
      <el-table :data="roles" v-loading="loading" size="small" highlight-current-row
        :show-header="true" @current-change="pick" style="width:100%">
        <el-table-column label="角色" min-width="130">
          <template #default="{ row }">
            {{ row.roleName }}
            <el-tag v-if="row.builtIn" size="small" type="info" style="margin-left:4px">内置</el-tag>
            <div class="muted mono">{{ row.roleCode }}</div>
          </template>
        </el-table-column>
        <el-table-column label="权限" width="60" align="center">
          <template #default="{ row }">{{ row.permCount }}</template>
        </el-table-column>
        <el-table-column label="账号" width="60" align="center">
          <template #default="{ row }">{{ row.userCount }}</template>
        </el-table-column>
        <el-table-column label="" width="60" align="center">
          <template #default="{ row }">
            <el-button v-if="!row.builtIn" link type="danger" size="small"
              @click.stop="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p class="muted note">
        内置角色的权限策略随版本发布维护；平台管理员（全权限通配）不可编辑。
      </p>
    </div>

    <!-- 右：权限授权 -->
    <div class="right">
      <template v-if="cur">
        <div class="bar">
          <b>{{ cur.roleName }}</b>
          <span class="muted">作用域：{{ SCOPE[cur.roleScope] || '-' }}</span>
          <div class="spacer"></div>
          <el-button size="small" @click="checkAll(true)">全选</el-button>
          <el-button size="small" @click="checkAll(false)">清空</el-button>
          <el-button size="small" type="primary" :disabled="!editable" :loading="saving"
            @click="save">保存权限</el-button>
        </div>

        <el-alert v-if="!editable" type="warning" :closable="false" style="margin-bottom:10px"
          :title="cur.roleCode === 'platform_admin'
            ? '内置平台管理员角色拥有全部权限点，不允许修改。'
            : `你当前也属于「${cur.roleName}」，为避免把自己锁在门外，不能修改本角色的权限。`" />

        <div class="tree-box" v-loading="treeLoading">
          <el-tree ref="treeRef" :data="tree" node-key="id" show-checkbox
            :expand-on-click-node="false" default-expand-all
            :props="{ label: 'permName', children: 'children' }">
            <template #default="{ data }">
              <span class="node">
                <span>{{ data.permName }}</span>
                <code>{{ data.permCode }}</code>
                <em v-if="data.permType === 1" class="menu-tag">菜单</em>
              </span>
            </template>
          </el-tree>
        </div>
        <p class="muted note">
          勾选一级菜单会连带授予其下全部动作点；保存时后端自动补齐父级菜单，未勾选的页面不会进入该角色的菜单树。
        </p>
      </template>
      <el-empty v-else description="左侧选择一个角色开始授权" />
    </div>

    <!-- 新建角色 -->
    <el-dialog v-model="createShow" title="新建自定义角色" width="460px">
      <el-form :model="form" label-width="88px">
        <el-form-item label="角色编码" required>
          <el-input v-model="form.roleCode" placeholder="如 ops_auditor，全站唯一" />
        </el-form-item>
        <el-form-item label="角色名称" required>
          <el-input v-model="form.roleName" placeholder="展示名，如 运维审计员" />
        </el-form-item>
        <el-form-item label="作用域">
          <el-select v-model="form.roleScope" style="width:100%">
            <el-option label="平台侧（空管/运维/管理）" :value="1" />
            <el-option label="商家侧（租户岗位）" :value="2" />
            <el-option label="客户端" :value="3" />
          </el-select>
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort" :min="1" :max="200" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createShow = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const SCOPE: Record<number, string> = { 1: '平台侧', 2: '商家侧', 3: '客户端' }

const roles = ref<any[]>([])
const tree = ref<any[]>([])
const cur = ref<any>(null)
const loading = ref(false)
const treeLoading = ref(false)
const saving = ref(false)
const treeRef = ref<any>(null)
const originIds = ref<number[]>([])

const editable = computed(() => !!cur.value
  && cur.value.roleCode !== 'platform_admin'
  && !userStore.roles.includes(cur.value.roleCode))

async function loadRoles(keepCurrent = true) {
  loading.value = true
  try {
    roles.value = (await request('/system/roles')).list
    if (keepCurrent && cur.value) {
      const again = roles.value.find((r) => r.id === cur.value.id)
      if (again) { pick(again) } else { cur.value = null }
    }
  } finally {
    loading.value = false
  }
}

/** 只回显"叶子动作点"：el-tree 的级联会把勾中的父菜单自动扩成全选，导致部分授权被显示成全部授权 */
function leafIds(ids: Set<number>, nodes: any[], out: number[] = []) {
  for (const n of nodes) {
    if (n.children?.length) leafIds(ids, n.children, out)
    else if (ids.has(n.id)) out.push(n.id)
  }
  return out
}
async function pick(row: any) {
  if (!row) return
  cur.value = row
  treeLoading.value = true
  try {
    const d: any = await request(`/system/roles/${row.id}/permissions`)
    originIds.value = d.permissionIds
    const set = new Set<number>(originIds.value)
    await nextTick()
    treeRef.value?.setCheckedKeys(leafIds(set, tree.value))
  } finally {
    treeLoading.value = false
  }
}

async function save() {
  if (!cur.value) return
  // el-tree 级联态：getCheckedKeys=全选中的节点，getHalfCheckedKeys=半选中的父菜单；
  // 两者相加即"该角色应拥有的权限点"，父菜单补齐后端也会再做一次兜底。
  const checked = new Set<number>([
    ...treeRef.value.getCheckedKeys(),
    ...treeRef.value.getHalfCheckedKeys(),
  ])
  // 原授权里"只给了菜单节点、其下没有任何动作点"的历史数据，el-tree 表达不了，按原样保留
  const origin = new Set<number>(originIds.value)
  const keep = [...origin].filter((id) => {
    const leaves = descendantLeaves(id)
    return leaves.length > 0 && !leaves.some((l) => origin.has(l))
  })
  saving.value = true
  try {
    const ids = [...new Set([...checked, ...keep])].sort((a, b) => a - b)
    if (!ids.length) return ElMessage.warning('至少勾选一个权限点')
    await request(`/system/roles/${cur.value.id}/permissions`,
      { method: 'PUT', data: { permissionIds: ids } })
    ElMessage.success(`权限已保存（共 ${ids.length} 个权限点），相关账号重新进入系统即生效`)
    loadRoles()
  } finally {
    saving.value = false
  }
}

/** 取某节点下的全部动作点（叶子）id；非菜单节点返回空数组 */
function descendantLeaves(id: number): number[] {
  const find = (nodes: any[]): any => {
    for (const n of nodes) {
      if (n.id === id) return n
      const hit = n.children?.length ? find(n.children) : null
      if (hit) return hit
    }
    return null
  }
  const node = find(tree.value)
  if (!node?.children?.length) return []
  const out: number[] = []
  const walk = (ns: any[]) => ns.forEach((n) => {
    if (n.children?.length) walk(n.children)
    else out.push(n.id)
  })
  walk(node.children)
  return out
}

function checkAll(v: boolean) {
  const flat: number[] = []
  const walk = (ns: any[]) => ns.forEach((n) => { flat.push(n.id); if (n.children) walk(n.children) })
  walk(tree.value)
  treeRef.value.setCheckedKeys(v ? flat : [])
}

// ---- 新建 / 删除 ----
const createShow = ref(false)
const form = reactive<any>({ roleCode: '', roleName: '', roleScope: 2, sort: 50, remark: '' })
function openCreate() {
  Object.assign(form, { roleCode: '', roleName: '', roleScope: 2, sort: 50, remark: '' })
  createShow.value = true
}
async function create() {
  if (!form.roleCode || !form.roleName) return ElMessage.warning('角色编码与名称必填')
  saving.value = true
  try {
    await request('/system/roles', { method: 'POST', data: { ...form } })
    ElMessage.success('角色已创建，请在右侧勾选权限')
    createShow.value = false
    await loadRoles(false)
    const r = roles.value.find((x) => x.roleCode === form.roleCode)
    if (r) pick(r)
  } finally {
    saving.value = false
  }
}
async function remove(row: any) {
  await ElMessageBox.confirm(
    `确认删除角色「${row.roleName}」？该角色下若还有账号需要先解绑。`, '删除确认',
    { type: 'warning' })
  await request(`/system/roles/${row.id}`, { method: 'DELETE' })
  ElMessage.success('角色已删除')
  if (cur.value?.id === row.id) cur.value = null
  loadRoles()
}

onMounted(async () => {
  tree.value = (await request('/system/permissions')).tree
  await loadRoles(false)
  if (roles.value.length) pick(roles.value.find((r) => !r.builtIn) || roles.value[0])
})

</script>

<style scoped>
.wrap { display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start; }
.left, .right { border: 1px solid #ebeef5; border-radius: 6px; padding: 12px; }
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.spacer { flex: 1; }
.muted { color: #909399; }
.note { font-size: 12px; line-height: 1.7; margin: 8px 0 0; }
.tree-box { max-height: 520px; overflow: auto; border: 1px dashed #ebeef5; padding: 8px; }
.node { display: flex; align-items: center; gap: 8px; }
.node code { font-size: 11px; color: #909399; background: #f5f7fa; padding: 1px 5px; border-radius: 3px; }
.menu-tag { font-style: normal; font-size: 11px; color: #c8a45c; }
</style>
