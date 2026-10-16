<template>
  <div class="vio">
    <div class="bar">
      <el-select v-model="statusFilter" size="small" style="width: 120px" @change="load">
        <el-option :value="''" label="全部状态" />
        <el-option :value="1" label="未处理" />
        <el-option :value="2" label="处理中" />
        <el-option :value="3" label="已归档" />
      </el-select>
      <el-select v-model="typeFilter" size="small" clearable placeholder="全部类型" style="width: 130px">
        <el-option v-for="(n, t) in VIO_TYPE" :key="t" :value="Number(t)" :label="n" />
      </el-select>
      <el-input v-model="keyword" size="small" clearable placeholder="编号 / 机号 / 机长 / 描述" style="width: 210px" />
      <div class="spacer" />
      <el-button size="small" @click="load">刷新</el-button>
      <el-button v-if="can('monitor:violation')" size="small" type="primary" plain :disabled="!rows.length"
        @click="exportReport">监管上报清单</el-button>
    </div>

    <div class="stats">
      <div v-for="s in stats" :key="s.label" class="stat">
        <label>{{ s.label }}</label><b class="num">{{ s.value }}</b>
      </div>
    </div>

    <el-table :data="filtered" v-loading="loading" stripe size="small">
      <el-table-column prop="violation_no" label="记录编号" width="170" class-name="num" />
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.violation_type === 1 ? 'danger' : 'warning'" effect="plain">
            {{ row.type_name }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="violation_time" label="发生时间" width="150" class-name="num" />
      <el-table-column label="主体" min-width="170">
        <template #default="{ row }">
          <div class="num">{{ row.aircraft_no || '未登记机号' }} · {{ row.pilot_name || '无机长' }}</div>
          <div class="muted">{{ row.plan_no || '—' }} · {{ row.merchant_name || '—' }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="违规事实" min-width="230" show-overflow-tooltip />
      <el-table-column label="处罚" min-width="150">
        <template #default="{ row }">
          <span v-if="row.penalty_type">{{ PENALTY[row.penalty_type] }}
            <span v-if="row.penalty_type === 2" class="num">{{ row.penalty_amount }}元</span>
            <span v-if="row.penalty_type === 3" class="num">{{ row.penalty_days }}天</span>
            <span class="muted">{{ row.penalty_reason || '' }}</span>
          </span>
          <span v-else class="muted">未处罚</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 3 ? 'success' : row.status === 2 ? 'warning' : 'danger'">
            {{ VIO_STATUS[row.status] || '未处理' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status !== 3 && can('monitor:violation')" link type="primary" size="small"
            @click="openVio(row)">处罚</el-button>
          <el-button v-if="row.notice_url" link type="primary" size="small" @click="openNotice(row)">通知书</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !filtered.length" description="没有符合条件的违规记录" :image-size="70" />
    <p class="muted note">
      违规由实时监控/仿真的合规监测逐帧自动入账（闯禁飞区、偏航、超时、超高、电子围栏）。
      作出处罚后状态归档，并向机长送达《违规处理通知书》；台账可导出为监管上报清单。
    </p>

    <el-dialog v-model="vioShow" title="违规处罚" width="480px">
      <p class="tip">对「{{ cur?.violation_no }} · {{ cur?.type_name }}（{{ cur?.aircraft_no }} / {{ cur?.pilot_name }}）」作出处罚：</p>
      <el-form label-width="80px">
        <el-form-item label="处罚类型">
          <el-select v-model="vioForm.penaltyType" style="width: 100%">
            <el-option v-for="(n, t) in PENALTY" :key="t" :value="Number(t)" :label="n" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="vioForm.penaltyType === 2" label="金额(元)">
          <el-input-number v-model="vioForm.penaltyAmount" :min="0" :max="100000" />
        </el-form-item>
        <el-form-item v-if="vioForm.penaltyType === 3" label="暂停天数">
          <el-input-number v-model="vioForm.penaltyDays" :min="1" :max="365" />
        </el-form-item>
        <el-form-item label="事由">
          <el-input v-model="vioForm.reason" type="textarea" :rows="3" placeholder="写入通知书的处罚事由" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="vioShow = false">取消</el-button>
        <el-button size="small" type="primary" :loading="busy" @click="saveVio">确认处罚并送达</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="noticeShow" title="违规处理通知书" width="560px">
      <div v-if="notice" class="notice">
        <h4>违规处理通知书</h4>
        <p class="num no">编号：{{ notice.noticeNo }}</p>
        <p><label>被处理人</label>{{ notice.pilotName || '—' }}（{{ notice.merchantName || '—' }}）</p>
        <p><label>违规事实</label>{{ notice.description || notice.typeName }}</p>
        <p><label>发生时间</label><span class="num">{{ notice.violationTime }}</span>
          <span v-if="notice.lng" class="muted">（{{ notice.lng }}, {{ notice.lat }}）</span></p>
        <p><label>处理决定</label><b>{{ notice.penaltyName }}</b>　事由：{{ notice.penaltyReason || '—' }}</p>
        <p><label>签发</label>{{ notice.issuerName || '平台运营' }} · <span class="num">{{ notice.issuedAt }}</span></p>
        <p class="appeal">{{ notice.appeal }}</p>
      </div>
      <template #footer>
        <el-button size="small" @click="copyNotice">复制全文</el-button>
        <el-button size="small" type="primary" @click="noticeShow = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'
import { useUserStore } from '@/stores/user'
import { today } from '@/utils/date'

const VIO_TYPE: Record<number, string> = {
  1: '闯入禁飞区', 2: '航线偏航', 3: '超时飞行', 4: '超高飞行', 5: '无证飞行', 6: '其他违规',
}
const VIO_STATUS: Record<number, string> = { 1: '未处理', 2: '处理中', 3: '已归档' }
const PENALTY: Record<number, string> = { 1: '警告', 2: '罚款', 3: '暂停服务', 4: '永久封禁' }

const userStore = useUserStore()
const can = (c: string) => userStore.hasPerm(c)

const rows = ref<any[]>([])
const loading = ref(false)
const busy = ref(false)
const statusFilter = ref<number | ''>('')
const typeFilter = ref<number | ''>('')
const keyword = ref('')

async function load() {
  loading.value = true
  try {
    rows.value = await request<any[]>('/aux/violations',
      { params: statusFilter.value === '' ? {} : { status: statusFilter.value } })
  } finally { loading.value = false }
}

const filtered = computed(() => rows.value.filter((v) => {
  if (typeFilter.value !== '' && v.violation_type !== typeFilter.value) return false
  const k = keyword.value.trim()
  if (!k) return true
  return [v.violation_no, v.aircraft_no || '', v.pilot_name || '', v.description || '', v.plan_no || '']
    .some((s) => String(s).includes(k))
}))

const stats = computed(() => {
  const all = filtered.value
  const open = all.filter((v) => v.status !== 3).length
  const punished = all.filter((v) => v.penalty_type).length
  const fine = all.reduce((s, v) => s + (Number(v.penalty_amount) || 0), 0)
  return [
    { label: '记录数', value: all.length },
    { label: '待处置', value: open },
    { label: '已处罚', value: punished },
    { label: '处罚率', value: all.length ? Math.round((punished / all.length) * 100) + '%' : '—' },
    { label: '罚款合计(元)', value: fine.toFixed(2) },
  ]
})

// ---------------- 处罚 ----------------
const cur = ref<any>(null)
const vioShow = ref(false)
const vioForm = reactive<any>({ penaltyType: 1, penaltyAmount: 0, penaltyDays: 0, reason: '' })
function openVio(row: any) {
  cur.value = row
  Object.assign(vioForm, { penaltyType: 1, penaltyAmount: 0, penaltyDays: 0, reason: '' })
  vioShow.value = true
}
async function saveVio() {
  if (!vioForm.reason.trim()) { ElMessage.warning('请填写处罚事由，通知书要写明依据'); return }
  busy.value = true
  try {
    await request(`/aux/violations/${cur.value.id}/penalty`, { method: 'POST', data: { ...vioForm } })
    ElMessage.success('处罚已记录，通知书已送达机长')
    vioShow.value = false
    await load()
  } finally { busy.value = false }
}

// ---------------- 通知书 ----------------
const noticeShow = ref(false)
const notice = ref<any>(null)
async function openNotice(row: any) {
  notice.value = await request<any>(`/aux/violations/${row.id}/notice`)
  noticeShow.value = true
}
async function copyNotice() {
  const text = notice.value?.body || ''
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('通知书全文已复制')
  } catch {
    ElMessage.warning('浏览器不允许剪贴板访问，可手动选择文本复制')
  }
}

// ---------------- 监管上报 ----------------
function exportReport() {
  const head = ['记录编号', '违规类型', '发生时间', '飞行器', '机长', '商家', '飞行计划', '违规事实', '处罚决定', '状态']
  const lines = [head].concat(filtered.value.map((v) => [
    v.violation_no, v.type_name, v.violation_time, v.aircraft_no || '', v.pilot_name || '',
    v.merchant_name || '', v.plan_no || '', v.description || '',
    v.penalty_type ? `${PENALTY[v.penalty_type]}${v.penalty_type === 2 ? ` ${v.penalty_amount}元` : ''}` : '未处罚',
    VIO_STATUS[v.status] || '未处理',
  ]))
  // BOM 不能少：没有它 Excel 打开中文列会乱码，报送时还要手工转一次
  const csv = '﻿' + lines.map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `天枢-违规监管上报-${today()}.csv`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success(`已生成上报清单（${filtered.value.length} 条）`)
}

onMounted(load)
</script>

<style lang="scss" scoped>
.bar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.spacer { flex: 1; }
.stats { display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.stat {
  flex: 1; min-width: 110px; background: var(--ts-bg); border: 1px solid var(--ts-border);
  border-radius: 4px; padding: 8px 10px;
}
.stat label { display: block; font-size: 11.5px; color: var(--ts-text-secondary); }
.stat b { font-size: 17px; color: var(--ts-primary-dark); }
.muted { color: var(--ts-text-secondary); font-size: 12px; }
.tip { margin: 0 0 10px; font-size: 13px; color: #606266; }
.note { margin: 10px 0 0; line-height: 1.6; }
.notice { border: 1px solid var(--ts-border); border-radius: 4px; padding: 14px 16px; background: #fff; }
.notice h4 { margin: 0 0 6px; text-align: center; letter-spacing: 2px; }
.notice .no { text-align: center; font-size: 12px; color: var(--ts-text-secondary); margin-bottom: 10px; }
.notice p { margin: 6px 0; font-size: 13px; line-height: 1.7; }
.notice label { color: var(--ts-text-secondary); margin-right: 8px; }
.notice .appeal { border-top: 1px dashed var(--ts-border); margin-top: 10px; padding-top: 8px; font-size: 12px; color: var(--ts-text-secondary); }
</style>
