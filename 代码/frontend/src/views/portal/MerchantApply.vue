<template>
  <div>
    <!-- 已有申请：先看状态，状态决定还能不能改 -->
    <div v-if="loading" class="ts-card" v-loading="true" style="min-height:160px"></div>
    <template v-else-if="apply">
      <div class="ts-card status-card">
        <div class="head">
          <div>
            <h3>{{ apply.merchantName }}</h3>
            <p class="muted">申请编号 {{ apply.merchantNo }} · 提交于 {{ (apply.createdAt || '').slice(0, 16) }}</p>
          </div>
          <el-tag :type="TAG[apply.auditStatus] || 'info'" size="large">{{ apply.auditStatusName }}</el-tag>
        </div>
        <el-descriptions :column="2" border style="margin-top:14px">
          <el-descriptions-item label="统一社会信用代码">{{ apply.unifiedCreditCode }}</el-descriptions-item>
          <el-descriptions-item label="法人代表">{{ apply.legalPerson }}</el-descriptions-item>
          <el-descriptions-item label="联系电话">{{ apply.contactPhone }}</el-descriptions-item>
          <el-descriptions-item label="资质到期">{{ apply.expireAt || '未填写' }}</el-descriptions-item>
          <el-descriptions-item label="服务区域" :span="2">
            {{ apply.serviceDistricts?.length ? apply.serviceDistricts.join('、') : '未填写' }}
          </el-descriptions-item>
          <el-descriptions-item v-if="apply.auditOpinion" label="审核意见" :span="2">
            {{ apply.auditOpinion }}
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="apply.auditStatus === 1" type="info" :closable="false" show-icon style="margin-top:14px"
          title="平台正在核验资质，通过后会以站内信通知您；审核期间不能重复提交"
        />
        <el-alert
          v-else-if="apply.auditStatus === 2" type="success" :closable="false" show-icon style="margin-top:14px"
          title="资质已通过核验，该商家已纳入运营承接范围，可用当前账号登录商家工作台接单与调度"
        />
        <el-button v-if="apply.auditStatus === 3" type="primary" style="margin-top:14px" @click="reopen">
          修改材料后重新提交
        </el-button>
      </div>
    </template>

    <!-- 无申请 / 被驳回后重提 -->
    <div v-else class="ts-card">
      <h3 class="title">{{ resubmit ? '重新提交入驻申请' : '商家入驻申请' }}</h3>
      <p class="muted tip">
        提交后由平台核验资质；核验通过，这个账号会自动升级为该商家的管理员账号（原客户身份保留，仍可正常下单）。
      </p>
      <el-form :model="form" label-width="130px" style="max-width:620px">
        <el-form-item label="企业名称" required>
          <el-input v-model="form.merchantName" maxlength="100" placeholder="与营业执照一致" />
        </el-form-item>
        <el-form-item label="统一社会信用代码" required>
          <el-input v-model="form.unifiedCreditCode" maxlength="18" placeholder="18 位，营业执照左上角" />
        </el-form-item>
        <el-form-item label="法人代表" required>
          <el-input v-model="form.legalPerson" maxlength="50" />
        </el-form-item>
        <el-form-item label="企业联系电话" required>
          <el-input v-model="form.contactPhone" maxlength="11" placeholder="11 位手机号，用于审核结果联系" />
        </el-form-item>
        <el-form-item label="可服务区域">
          <el-select v-model="form.serviceDistricts" multiple filterable allow-create default-first-option
            placeholder="可输入后回车添加" style="width:100%">
            <el-option v-for="d in DISTRICTS" :key="d" :label="d" :value="d" />
          </el-select>
        </el-form-item>
        <el-form-item label="资质有效期">
          <el-date-picker v-model="form.expireAt" type="date" placeholder="经营许可证 / 空域使用权到期日"
            format="YYYY-MM-DD" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
        <div class="pricing">
          <div class="pricing-item">
            <span>基础运价（元/km）</span>
            <el-input-number v-model="form.pricing.basePerKm" :min="0" :step="0.1" :precision="2" size="small" />
          </div>
          <div class="pricing-item">
            <span>重量附加（元/kg）</span>
            <el-input-number v-model="form.pricing.perKg" :min="0" :step="0.1" :precision="2" size="small" />
          </div>
          <div class="pricing-item">
            <span>加急倍率</span>
            <el-input-number v-model="form.pricing.urgentRate" :min="1" :step="0.1" :precision="2" size="small" />
          </div>
        </div>
        <p class="muted pricing-note">运价留空即用平台建议值，审核通过后可在商家侧再调整。</p>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="submit">提交申请</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { applyMerchantApi, myMerchantApplyApi, type MerchantVO } from '@/api/merchant'

const DISTRICTS = ['鹿城区', '瓯海区', '龙湾区', '洞头区', '乐清市', '瑞安市']
const TAG: Record<number, string> = { 1: 'warning', 2: 'success', 3: 'danger', 4: 'info' }

const loading = ref(true)
const submitting = ref(false)
const apply = ref<MerchantVO | null>(null)
// 被驳回时不清空申请记录（要留着审核意见给人看），只把表单打开
const resubmit = ref(false)

const form = reactive({
  merchantName: '',
  unifiedCreditCode: '',
  legalPerson: '',
  contactPhone: '',
  serviceDistricts: [] as string[],
  expireAt: '' as string | null,
  pricing: { basePerKm: 3.5, perKg: 1.2, urgentRate: 1.5 },
})

async function load() {
  loading.value = true
  try {
    apply.value = await myMerchantApplyApi()
    if (apply.value && apply.value.auditStatus !== 3) resubmit.value = false
  } finally {
    loading.value = false
  }
}

function reopen() {
  const a = apply.value
  if (a) {
    form.merchantName = a.merchantName
    form.unifiedCreditCode = a.unifiedCreditCode
    form.legalPerson = a.legalPerson
    form.serviceDistricts = [...(a.serviceDistricts || [])]
    if (a.pricing) form.pricing = { ...a.pricing }
  }
  resubmit.value = true
  apply.value = null
}

async function submit() {
  if (!form.merchantName.trim() || !form.legalPerson.trim()) {
    return ElMessage.warning('企业名称与法人代表必填')
  }
  if (!/^[0-9A-Z]{18}$/.test(form.unifiedCreditCode.trim().toUpperCase())) {
    return ElMessage.warning('统一社会信用代码需为 18 位数字或大写字母')
  }
  if (!/^1\d{10}$/.test(form.contactPhone)) {
    return ElMessage.warning('请填写 11 位手机号')
  }
  submitting.value = true
  try {
    await applyMerchantApi({
      merchantName: form.merchantName.trim(),
      unifiedCreditCode: form.unifiedCreditCode.trim().toUpperCase(),
      legalPerson: form.legalPerson.trim(),
      contactPhone: form.contactPhone,
      serviceDistricts: form.serviceDistricts,
      expireAt: form.expireAt || null,
      pricing: form.pricing,
    })
    ElMessage.success('申请已提交，平台核验后会通知您')
    resubmit.value = false
    form.contactPhone = ''
    await load()
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<style lang="scss" scoped>
.title { margin: 0 0 6px; color: var(--ts-primary-dark); }
.tip { font-size: 12.5px; line-height: 1.7; margin: 0 0 18px; }
.muted { color: var(--ts-text-secondary); }
.status-card .head { display: flex; justify-content: space-between; align-items: flex-start; }
.status-card h3 { margin: 0 0 4px; color: var(--ts-primary-dark); }
.pricing { display: flex; gap: 18px; flex-wrap: wrap; margin: 0 0 6px 130px; }
.pricing-item { font-size: 12.5px; color: var(--ts-text-regular); }
.pricing-item span { display: block; margin-bottom: 4px; }
.pricing-note { font-size: 12px; margin: 0 0 16px 130px; }
</style>
