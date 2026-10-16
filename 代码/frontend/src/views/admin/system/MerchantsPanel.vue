<template>
  <div>
    <div class="bar">
      <el-select v-model="query.auditStatus" size="small" style="width:130px" @change="reload">
        <el-option label="审核中" :value="1" />
        <el-option label="已通过" :value="2" />
        <el-option label="已驳回" :value="3" />
        <el-option label="待提交" :value="0" />
        <el-option label="停用注销" :value="4" />
        <el-option label="全部状态" value="" />
      </el-select>
      <el-input
        v-model="query.keyword" size="small" style="width:240px" clearable
        placeholder="企业名称 / 商家编号 / 信用代码" @keyup.enter="reload" @clear="reload"
      />
      <el-button size="small" type="primary" @click="reload">查询</el-button>
      <div class="spacer"></div>
      <span class="muted">共 {{ total }} 条</span>
    </div>

    <el-table :data="rows" v-loading="loading" stripe size="small">
      <el-table-column prop="merchantNo" label="商家编号" width="140" />
      <el-table-column prop="merchantName" label="企业名称" min-width="180" />
      <el-table-column prop="unifiedCreditCode" label="统一社会信用代码" width="190">
        <template #default="{ row }"><span class="mono">{{ row.unifiedCreditCode }}</span></template>
      </el-table-column>
      <el-table-column prop="legalPerson" label="法人" width="90" />
      <el-table-column prop="contactPhone" label="联系电话" width="120" />
      <el-table-column label="服务区域" min-width="140">
        <template #default="{ row }">{{ row.serviceDistricts?.join('、') || '—' }}</template>
      </el-table-column>
      <el-table-column prop="expireAt" label="资质到期" width="110">
        <template #default="{ row }">{{ row.expireAt || '—' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="TAG[row.auditStatus] || 'info'">{{ row.auditStatusName }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <template v-if="row.auditStatus === 1">
            <el-button link type="success" size="small" @click="openAudit(row, 1)">通过</el-button>
            <el-button link type="danger" size="small" @click="openAudit(row, 2)">驳回</el-button>
          </template>
          <el-button v-else link size="small" @click="detail(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      style="margin-top:12px;justify-content:flex-end" layout="prev, pager, next, sizes, total"
      :total="total" v-model:current-page="query.page" v-model:page-size="query.pageSize"
      :page-sizes="[10, 20, 50]" @current-change="load" @size-change="reload"
    />

    <el-dialog v-model="auditShow" :title="`${conclusion === 1 ? '通过' : '驳回'}入驻申请：${cur?.merchantName || ''}`" width="480px">
      <el-alert
        v-if="conclusion === 1" type="success" :closable="false" show-icon style="margin-bottom:12px"
        title="通过后该商家进入运营承接范围（客户下单可被它接走），申请人同时升级为该商家的管理员账号"
      />
      <el-alert
        v-else type="warning" :closable="false" show-icon style="margin-bottom:12px"
        title="驳回原因会通过站内信发给申请方，请写清楚缺哪一份材料"
      />
      <el-descriptions v-if="cur" :column="1" border size="small" style="margin-bottom:12px">
        <el-descriptions-item label="信用代码">{{ cur.unifiedCreditCode }}</el-descriptions-item>
        <el-descriptions-item label="法人代表">{{ cur.legalPerson }}</el-descriptions-item>
        <el-descriptions-item label="联系电话">{{ cur.contactPhone }}</el-descriptions-item>
        <el-descriptions-item label="资质到期">{{ cur.expireAt || '未填写' }}</el-descriptions-item>
      </el-descriptions>
      <el-input
        v-model="opinion" type="textarea" :rows="3" maxlength="500" show-word-limit
        :placeholder="conclusion === 1 ? '可留空（默认记录：资质核验通过）' : '必填：驳回原因'"
      />
      <template #footer>
        <el-button @click="auditShow = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="confirm">确认{{ conclusion === 1 ? '通过' : '驳回' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailShow" title="申请详情" width="480px">
      <el-descriptions v-if="cur" :column="1" border size="small">
        <el-descriptions-item label="商家编号">{{ cur.merchantNo }}</el-descriptions-item>
        <el-descriptions-item label="企业名称">{{ cur.merchantName }}</el-descriptions-item>
        <el-descriptions-item label="审核状态">{{ cur.auditStatusName }}</el-descriptions-item>
        <el-descriptions-item label="审核意见">{{ cur.auditOpinion || '—' }}</el-descriptions-item>
        <el-descriptions-item label="审核时间">{{ cur.auditedAt || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { auditMerchantApi, merchantsAuditListApi, type MerchantQuery, type MerchantVO } from '@/api/merchant'

const TAG: Record<number, string> = { 0: 'info', 1: 'warning', 2: 'success', 3: 'danger', 4: 'info' }

const rows = ref<MerchantVO[]>([])
const total = ref(0)
const loading = ref(false)
// 默认停在「审核中」：审核台打开就该看见待办，而不是一屏历史通过记录
const query = reactive<MerchantQuery & { page: number; pageSize: number }>({
  page: 1, pageSize: 10, auditStatus: 1, keyword: '',
})

const auditShow = ref(false)
const detailShow = ref(false)
const saving = ref(false)
const cur = ref<MerchantVO | null>(null)
const conclusion = ref<1 | 2>(1)
const opinion = ref('')

async function load() {
  loading.value = true
  try {
    const r = await merchantsAuditListApi(query)
    rows.value = r.list
    total.value = r.total
  } finally {
    loading.value = false
  }
}
function reload() { query.page = 1; load() }

function openAudit(row: MerchantVO, result: 1 | 2) {
  cur.value = row
  conclusion.value = result
  opinion.value = ''
  auditShow.value = true
}
function detail(row: MerchantVO) {
  cur.value = row
  detailShow.value = true
}

async function confirm() {
  if (!cur.value) return
  if (conclusion.value === 2 && !opinion.value.trim()) {
    return ElMessage.warning('驳回必须填写原因')
  }
  saving.value = true
  try {
    await auditMerchantApi(cur.value.id, conclusion.value, opinion.value.trim() || undefined)
    ElMessage.success(conclusion.value === 1 ? '已通过，商家进入承接池' : '已驳回，结果已通知申请方')
    auditShow.value = false
    load()
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.spacer { flex: 1; }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.mono { font-family: Consolas, monospace; }
</style>
