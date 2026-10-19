<template>
  <div>
    <div class="bar">
      <el-select v-model="typeFilter" size="small" clearable placeholder="全部类型" style="width: 130px">
        <el-option v-for="(n, t) in MT_TYPE" :key="t" :value="Number(t)" :label="n" />
      </el-select>
      <el-select v-model="statusFilter" size="small" clearable placeholder="全部状态" style="width: 120px">
        <el-option v-for="(n, t) in MT_STATUS" :key="t" :value="Number(t)" :label="n" />
      </el-select>
      <div class="spacer" />
      <el-button size="small" @click="load">刷新</el-button>
      <el-button v-if="can('resource:aircraft')" type="primary" size="small" @click="open">登记维保/故障</el-button>
    </div>

    <el-table :data="filtered" stripe v-loading="loading" size="small">
      <el-table-column label="飞机" width="130">
        <template #default="{ row }"><b class="num">{{ acName[row.aircraft_id] || `#${row.aircraft_id}` }}</b></template>
      </el-table-column>
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.record_type === 2 ? 'danger' : 'primary'" effect="plain">
            {{ MT_TYPE[row.record_type] || '其他' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="fault_type" label="故障/维保项" width="140" />
      <el-table-column prop="description" label="问题描述" min-width="200" show-overflow-tooltip />
      <el-table-column prop="result" label="处理结果" min-width="160">
        <template #default="{ row }">{{ row.result || '—' }}</template>
      </el-table-column>
      <el-table-column label="登记时间" width="150" class-name="num">
        <template #default="{ row }">{{ row.started_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="完成时间" width="150" class-name="num">
        <template #default="{ row }">{{ row.finished_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 3 ? 'success' : 'warning'">
            {{ MT_STATUS[row.status] || '待处理' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="can('resource:aircraft')" label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status !== 3" link type="success" size="small" @click="finish(row)">完结</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !filtered.length" description="暂无维保记录" :image-size="70" />
    <p class="muted note">
      登记「定期维保」或「故障维修」后该机会自动置为维保中，调度台不再推荐它派单；「巡检」不接地，
      登记完照样能派单。完结后若这台机没有在办的其它维保记录，才恢复闲置（需要重新充满电再投用，
      见「飞行器台账」的一键充换电）。
    </p>

    <el-dialog v-model="show" title="登记维保/故障" width="460px">
      <el-form :model="form" label-width="84px" size="small">
        <el-form-item label="飞行器">
          <el-select v-model="form.aircraftId" style="width: 100%" filterable placeholder="选择机号">
            <el-option v-for="a in aircraft" :key="a.id" :label="`${a.aircraftNo} · ${a.model}`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="form.recordType">
            <el-radio v-for="(n, t) in MT_TYPE" :key="t" :value="Number(t)">{{ n }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="项目">
          <el-input v-model="form.faultType" placeholder="如：动力系统 / 电池 / 桨叶" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="show = false">取消</el-button>
        <el-button size="small" type="primary" :loading="busy" @click="submit">登记</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
import { useUserStore } from '@/stores/user'

// status 口径跟 maintenance_record 的 DDL 注释一致：1 待处理 2 处理中 3 已完成。
// 早先这里把 2 当"已完成"，完结按钮点了以后机器一直挂在维保中，调度台再也没推荐过它。
const MT_TYPE: Record<number, string> = { 1: '定期维保', 2: '故障维修', 3: '巡检' }
const MT_STATUS: Record<number, string> = { 1: '待处理', 2: '处理中', 3: '已完成' }

const userStore = useUserStore()
const can = (c: string) => userStore.hasPerm(c)
// 登记/完结会改 aircraft.status，父页台账表要跟着刷；不抛出去的话两边会显示不同状态
const emit = defineEmits<{ (e: 'changed'): void }>()

const list = ref<any[]>([])
const aircraft = ref<any[]>([])
const loading = ref(true)
const busy = ref(false)
const typeFilter = ref<number | ''>('')
const statusFilter = ref<number | ''>('')
const acName = computed(() => Object.fromEntries(aircraft.value.map((a) => [a.id, a.aircraftNo])))

const filtered = computed(() => list.value.filter((r) => {
  if (typeFilter.value !== '' && r.record_type !== typeFilter.value) return false
  if (statusFilter.value !== '' && r.status !== statusFilter.value) return false
  return true
}))

async function load() {
  loading.value = true
  try { list.value = await request('/aux/maintenance') } finally { loading.value = false }
}

const show = ref(false)
const form = reactive<any>({ aircraftId: null, recordType: 1, faultType: '', description: '' })
function open() {
  Object.assign(form, { aircraftId: aircraft.value[0]?.id ?? null, recordType: 1, faultType: '', description: '' })
  show.value = true
}
async function submit() {
  if (!form.aircraftId) return ElMessage.warning('请选择飞行器')
  if (!form.faultType.trim()) return ElMessage.warning('请填写故障/维保项目')
  busy.value = true
  try {
    await request('/aux/maintenance', { method: 'POST', data: { ...form } })
    ElMessage.success(form.recordType === 3 ? '巡检记录已登记（该机保持可派）' : '已登记，该机已置为维保中')
    show.value = false
    await load()
    emit('changed')          // 登记会改 aircraft.status，父页的台账表必须跟着刷
  } finally { busy.value = false }
}
async function finish(row: any) {
  const ok = await ElMessageBox.confirm(
    `确认「${acName.value[row.aircraft_id] || row.aircraft_id} · ${row.fault_type || '维保'}」已处理完成？`,
    '完结维保', { type: 'warning' }).catch(() => null)
  if (!ok) return
  await request(`/aux/maintenance/${row.id}`, { method: 'PUT', data: { status: 3, result: '已修复并测试正常' } })
  ElMessage.success('已完结')
  load()
  emit('changed')
}

onMounted(async () => {
  try {
    aircraft.value = (await request<{ list: any[] }>('/resource/aircraft')).list
    await load()
  } finally { loading.value = false }
})
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.spacer { flex: 1; }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.note { margin: 10px 0 0; line-height: 1.6; }
</style>
