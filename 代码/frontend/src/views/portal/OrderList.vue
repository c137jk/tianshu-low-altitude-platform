<template>
  <div>
    <div class="page-head">
      <h2>我的订单</h2>
      <div>
        <el-button @click="exportCsv">导出 Excel(CSV)</el-button>
        <el-button @click="openBatch">批量导入下单</el-button>
        <el-button type="primary" @click="$router.push('/portal/order/create')">+ 我要下单</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" @tab-change="load">
      <el-tab-pane v-for="t in ORDER_TABS" :key="String(t.value)" :label="t.label" :name="String(t.value)" />
    </el-tabs>

    <el-table v-loading="loading" :data="pageList" class="ts-card" @row-click="goDetail" row-class-name="clickable">
      <el-table-column label="订单号" prop="orderNo" width="190" />
      <el-table-column label="配送路线" min-width="200">
        <template #default="{ row }">
          <div class="route"><span class="dot send" />{{ row.senderAddress }}</div>
          <div class="route"><span class="dot recv" />{{ row.receiverAddress }}</div>
        </template>
      </el-table-column>
      <el-table-column label="物品" width="120">
        <template #default="{ row }">{{ row.goodsType }} · {{ row.weightKg }}kg</template>
      </el-table-column>
      <el-table-column label="金额" width="100">
        <template #default="{ row }"><span class="num">{{ yuan(row.totalAmount) }}</span></template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="ORDER_STATUS_MAP[row.status]?.type" size="small">{{ row.statusName }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="下单时间" width="160">
        <template #default="{ row }">{{ row.createdAt }}</template>
      </el-table-column>
      <el-table-column label="操作" width="210">
        <template #default="{ row }">
          <el-button link type="primary" @click.stop="goDetail(row)">详情</el-button>
          <el-button v-if="isMine(row) && row.status === 0" link type="warning" @click.stop="goDetail(row)">去支付</el-button>
          <el-button v-if="isMine(row) && row.status === 6" link type="success" @click.stop="onConfirm(row)">确认收货</el-button>
          <el-button v-if="isMine(row) && [0, 1].includes(row.status)" link type="danger" @click.stop="onCancel(row)">取消</el-button>
          <span v-if="!isMine(row) && [0, 1, 6].includes(row.status)" class="by-customer">由下单客户操作</span>
        </template>
      </el-table-column>
      <template #empty>暂无订单</template>
    </el-table>

    <el-pagination
      class="pager" background layout="prev, pager, next"
      :total="filtered.length" :page-size="pageSize" v-model:current-page="page"
    />

    <el-dialog v-model="batchVisible" title="批量导入下单" width="min(860px, 94vw)" :close-on-click-modal="false">
      <el-alert type="info" :closable="false" show-icon class="batch-tip">
        <template #title>一次最多 200 行，每单走的校验与手工下单完全一致（禁运品、服务空域、禁飞区、坐标）</template>
        <p>
          请先下载模板，按表头括号里的格式填写后上传 CSV（另存为时保持 UTF-8 或 GBK 都能识别）。
          建议先「仅校验」看一遍逐行结果，再「校验并导入」——校验不会生成任何订单。
          导入时失败的行会自动跳过，成功的行照常下单，互不影响。
        </p>
      </el-alert>

      <div class="batch-ops">
        <el-button :icon="Download" :loading="tplLoading" @click="downloadTemplate">下载模板</el-button>
        <el-upload
          ref="uploadRef" drag :auto-upload="false" :limit="1" accept=".csv,text/csv"
          :show-file-list="false" :on-change="onFileChange" :on-exceed="onExceed" class="uploader"
        >
          <span class="pick">{{ picked ? `已选择：${picked.name}（${(picked.size / 1024).toFixed(1)} KB）` : '点击或拖拽 CSV 文件到此处' }}</span>
        </el-upload>
        <el-button v-if="picked" link type="danger" @click="clearFile">清除</el-button>
      </div>

      <template v-if="result">
        <el-alert
          :type="result.failed ? 'warning' : 'success'" :closable="false" show-icon class="batch-sum"
          :title="summaryText"
        />
        <el-table :data="result.rows" size="small" max-height="320" border>
          <el-table-column label="行号" prop="line" width="64" align="center" />
          <el-table-column label="寄件人 → 收件人" prop="key" min-width="170" />
          <el-table-column label="结果" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="row.ok ? 'success' : 'danger'" size="small">{{ row.ok ? '通过' : '失败' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="260">
            <template #default="{ row }">
              <span v-if="row.ok" class="ok-text">
                {{ result.validateOnly ? `预估 ${yuan(row.totalAmount)}` : `已下单 ${row.orderNo}` }}
                <span v-if="result.validateOnly && row.distanceM" class="sub">/ {{ (row.distanceM / 1000).toFixed(2) }}km</span>
              </span>
              <span v-else class="fail-text">{{ row.reason }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <template #footer>
        <el-button @click="batchVisible = false">关闭</el-button>
        <el-button :disabled="!picked || !!busy" :loading="busy === 'validate'" @click="runBatch(true)">仅校验</el-button>
        <el-button type="primary" :disabled="!picked || !!busy" :loading="busy === 'import'" @click="runBatch(false)">
          校验并导入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, genFileId } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import type { UploadFile, UploadInstance, UploadRawFile } from 'element-plus'
import {
  listOrdersApi, cancelOrderApi, confirmOrderApi,
  batchImportOrdersApi, batchTemplateDownloadApi, type OrderVO, type BatchResult,
} from '@/api/order'
import { ORDER_STATUS_MAP, ORDER_TABS, ACTIVE_STATUS, yuan } from '@/constants/dict'
import { useUserStore } from '@/stores/user'
import { today } from '@/utils/date'

const router = useRouter()
const userStore = useUserStore()
// 后端对支付/取消/确认收货都验 customer_id == 当前登录人，但商家员工在本页能看到
// 本商家所有客户的订单（_scope_query 按 merchant 收口，不是按 customer）。按钮无差别
// 露出来的话，员工一点就是一句 403，看起来像页面坏了。
const isMine = (row: OrderVO) => row.customerId === userStore.userInfo?.id
const loading = ref(false)
const activeTab = ref('')
const page = ref(1)
const pageSize = 10
const orders = ref<OrderVO[]>([])

const filtered = computed(() => {
  if (activeTab.value === '') return orders.value
  if (activeTab.value === 'active') return orders.value.filter((o) => ACTIVE_STATUS.includes(o.status))
  return orders.value.filter((o) => o.status === Number(activeTab.value))
})
const pageList = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))

async function load() {
  page.value = 1
  loading.value = true
  try {
    const res = await listOrdersApi({ page: 1, pageSize: 100 })
    orders.value = res.list
  } finally {
    loading.value = false
  }
}

function goDetail(row: OrderVO) {
  router.push(`/portal/orders/${row.id}`)
}

async function onCancel(row: OrderVO) {
  let reason = ''
  try {
    const r = await ElMessageBox.prompt('请填写取消原因', '取消订单', {
      confirmButtonText: '确认取消', cancelButtonText: '再想想', inputValue: '客户主动取消',
    })
    reason = r.value || '客户主动取消'
  } catch { return }
  await cancelOrderApi(row.id, reason)
  ElMessage.success('订单已取消' + (row.paid ? '，支付金额将原路退回（Mock）' : ''))
  load()
}

async function onConfirm(row: OrderVO) {
  await ElMessageBox.confirm('确认已收到货物？', '确认收货', { type: 'success' })
  await confirmOrderApi(row.id)
  ElMessage.success('已确认收货，感谢使用')
  load()
}

function exportCsv() {
  const rows = filtered.value
  const head = ['订单号', '寄件地址', '收件地址', '物品类型', '重量kg', '金额', '状态', '下单时间']
  const lines = rows.map((o) => [o.orderNo, o.senderAddress, o.receiverAddress, o.goodsType,
    o.weightKg, o.totalAmount, o.statusName, o.createdAt]
    .map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
  const csv = '\uFEFF' + [head.join(','), ...lines].join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  a.download = `我的订单_${today()}.csv`
  a.click()
  ElMessage.success(`已导出 ${rows.length} 条`)
}

/** M-23 批量导入下单 */
const batchVisible = ref(false)
const uploadRef = ref<UploadInstance>()
const picked = ref<UploadRawFile | null>(null)
const result = ref<BatchResult | null>(null)
const busy = ref<'' | 'validate' | 'import'>('')
const tplLoading = ref(false)

const summaryText = computed(() => {
  const r = result.value
  if (!r) return ''
  if (r.validateOnly) {
    return `校验 ${r.total} 行：${r.succeeded} 行可下单，${r.failed} 行需要修改（校验不会生成订单）`
  }
  return `导入 ${r.total} 行：成功下单 ${r.succeeded} 行，跳过 ${r.failed} 行`
})

function openBatch() {
  batchVisible.value = true
}

function clearFile() {
  picked.value = null
  result.value = null
  uploadRef.value?.clearFiles()
}

function onFileChange(file: UploadFile) {
  picked.value = (file.raw as UploadRawFile) || null
  result.value = null
}

/** limit=1 时的替换上传：不清空的话第二次选文件会被 el-upload 直接丢掉 */
function onExceed(files: File[]) {
  uploadRef.value?.clearFiles()
  const f = files[0] as UploadRawFile
  f.uid = genFileId()
  uploadRef.value?.handleStart(f)
  picked.value = f
  result.value = null
}

async function downloadTemplate() {
  tplLoading.value = true
  try {
    await batchTemplateDownloadApi()
  } finally {
    tplLoading.value = false
  }
}

async function runBatch(validateOnly: boolean) {
  if (!picked.value) {
    ElMessage.warning('请先选择 CSV 文件')
    return
  }
  busy.value = validateOnly ? 'validate' : 'import'
  try {
    result.value = await batchImportOrdersApi(picked.value, validateOnly)
    if (!validateOnly && result.value.succeeded > 0) load()
  } catch {
    // 业务失败（表头不认识、超过 200 行、非客户账号）拦截器已经弹过 toast，
    // 这里只把结果表清空，免得留着上一次的结果误导判断
    result.value = null
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<style lang="scss" scoped>
.page-head { display: flex; justify-content: space-between; align-items: center; }
.page-head h2 { margin: 0; color: var(--ts-primary-dark); }
:deep(.clickable) { cursor: pointer; }
.route { font-size: 12.5px; color: var(--ts-text-regular); line-height: 1.8; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }
.dot.send { background: var(--ts-primary); }
.dot.recv { background: var(--ts-gold); }
.num { color: var(--ts-danger); font-weight: 700; }
.by-customer { font-size: 12px; color: var(--ts-text-secondary); }
.by-customer { font-size: 12px; color: var(--ts-text-secondary); margin-left: 6px; }
.pager { margin-top: 16px; justify-content: flex-end; }
.batch-tip p { margin: 4px 0 0; font-size: 12.5px; line-height: 1.7; font-weight: 400; }
.batch-ops { display: flex; align-items: center; gap: 12px; margin: 14px 0; }
.batch-ops .uploader { flex: 1; }
.batch-ops :deep(.el-upload-dragger) { padding: 16px; background: var(--ts-bg-soft, #fafbfe); }
.pick { font-size: 12.5px; color: var(--ts-text-regular); }
.batch-sum { margin-bottom: 10px; }
.ok-text { color: var(--ts-success); }
.fail-text { color: var(--ts-danger); }
.sub { color: var(--ts-text-secondary); font-size: 12px; }
</style>
