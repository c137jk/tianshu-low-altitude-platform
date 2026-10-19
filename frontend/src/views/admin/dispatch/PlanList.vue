<template>
  <div>
    <div class="ts-card filter-bar">
      <el-radio-group v-model="activeStatus" @change="reload">
        <el-radio-button v-for="t in PLAN_TABS" :key="String(t.value)" :value="t.value">
          {{ t.label }}
          <span v-if="t.value !== '' && counts[t.value] != null" class="cnt num">{{ counts[t.value] }}</span>
        </el-radio-button>
      </el-radio-group>
      <el-input v-model="keyword" placeholder="计划号 / 订单号 / 任务名" clearable style="width: 250px"
        @keyup.enter="reload" @clear="reload">
        <template #append><el-button :icon="Search" @click="reload" /></template>
      </el-input>
      <el-button :icon="Refresh" @click="reload">刷新</el-button>
    </div>

    <div class="ts-card" style="margin-top: 12px">
      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column label="计划号" min-width="190">
          <template #default="{ row }">
            <router-link :to="`/admin/dispatch/plans/${row.id}`" class="num link">{{ row.planNo }}</router-link>
          </template>
        </el-table-column>
        <el-table-column label="订单号" min-width="180">
          <template #default="{ row }"><span class="num sub">{{ row.orderNo }}</span></template>
        </el-table-column>
        <el-table-column label="任务" min-width="150" prop="taskName" show-overflow-tooltip />
        <el-table-column label="飞行器 / 机长" min-width="160">
          <template #default="{ row }">
            {{ row.aircraftNo || '—' }}<span class="sub"> / {{ row.pilotName || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="航程 / 航时" width="130">
          <template #default="{ row }">
            <span class="num">{{ fmtDistance(row.distanceM) }}</span>
            <span class="sub"> / {{ fmtDuration(row.durationSec) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="PLAN_STATUS_MAP[row.status]?.type" effect="light">{{ row.statusName }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="申报时间" width="165">
          <template #default="{ row }"><span class="num sub">{{ row.submittedAt || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="$router.push(`/admin/dispatch/plans/${row.id}`)">
              详情
            </el-button>
            <el-button v-if="row.status === 0" size="small" type="warning" @click="submit(row)">提交申报</el-button>
            <el-button v-if="row.status === 2 && !row.assignedAt && userStore.hasPerm('dispatch:assign')"
              size="small" type="primary" @click="assign(row)">下发机长</el-button>
            <el-button v-if="[0, 1, 2].includes(row.status)" size="small" @click="cancel(row)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination class="pager" layout="total, prev, pager, next, jumper"
        :total="total" :page-size="pageSize" :current-page="page" @current-change="onPage" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh } from '@element-plus/icons-vue'
import {
  listPlansApi, submitPlanApi, assignPlanApi, cancelPlanApi, type FlightPlanVO,
} from '@/api/dispatch'
import { PLAN_STATUS_MAP, PLAN_TABS, fmtDistance, fmtDuration } from '@/constants/dict'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const list = ref<FlightPlanVO[]>([])
const total = ref(0)
const counts = ref<Record<number, number>>({})
const page = ref(1)
const pageSize = ref(10)
// 默认页签按身份给：空管/审批岗进来就是待审批队列，其余看全量。
// 固定成 0（待申报）的话，空管切进来会是一张空表——四条待审批的计划明明在库里，
// 页面上却像"打不开"，很难归因。
const activeStatus = ref<number | ''>(userStore.hasPerm('approval:handle') ? 1 : '')
const keyword = ref('')
const loading = ref(false)

async function reload() {
  loading.value = true
  try {
    const res = await listPlansApi({
      page: page.value, pageSize: pageSize.value,
      status: activeStatus.value === '' ? '' : activeStatus.value,
      keyword: keyword.value || undefined,
    })
    list.value = res.list
    total.value = res.total
    counts.value = res.statusCounts || {}
  } finally {
    loading.value = false
  }
}

function onPage(p: number) { page.value = p; reload() }

async function submit(row: FlightPlanVO) {
  await ElMessageBox.confirm(`确认将计划 ${row.planNo} 提交空管申报？`, '提交申报', { type: 'info' })
  const ack = await submitPlanApi(row.id)
  ElMessage.success(`申报成功，申报号 ${ack.applyNo || ''}`)
  reload()
}

async function assign(row: FlightPlanVO) {
  await ElMessageBox.confirm(`确认将任务下发给机长 ${row.pilotName}？将推送 GCS 指令（Mock）。`, '下发机长', {
    type: 'warning', confirmButtonText: '确认下发',
  })
  const ack = await assignPlanApi(row.id)
  ElMessage.success(`已下发，指令号 ${ack.commandNo || ''}`)
  reload()
}

async function cancel(row: FlightPlanVO) {
  const { value } = await ElMessageBox.prompt('请填写取消原因（可选，直接确定则按默认原因）', '取消计划', {
    confirmButtonText: '确认取消', inputValue: '运营调整，取消本计划',
  })
  await cancelPlanApi(row.id, value || undefined)
  ElMessage.success('计划已取消')
  reload()
}

onMounted(reload)
</script>

<style lang="scss" scoped>
.filter-bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.cnt { font-size: 11px; color: var(--ts-gold); margin-left: 2px; }
.link { font-size: 12.5px; }
.sub { color: var(--ts-text-secondary); font-size: 12px; }
.pager { margin-top: 14px; justify-content: flex-end; }
</style>
