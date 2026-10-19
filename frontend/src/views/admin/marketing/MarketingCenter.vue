<template>
  <el-card shadow="never">
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>优惠券管理</b>
        <el-button type="primary" size="small" @click="show=true">创建优惠券</el-button>
      </div>
    </template>
    <el-table :data="list" stripe v-loading="loading">
      <el-table-column prop="coupon_name" label="券名称" width="180" />
      <el-table-column label="类型" width="110">
        <template #default="{ row }">{{ row.coupon_type===1?'满减券':'折扣券' }}</template>
      </el-table-column>
      <el-table-column label="规则">
        <template #default="{ row }">
          <span v-if="row.coupon_type===1">满{{ row.threshold }}减{{ row.amount }}</span>
          <span v-else>{{ Number(row.discount)*10 }}折</span>
        </template>
      </el-table-column>
      <el-table-column label="领取/发放" width="110">
        <template #default="{ row }">{{ row.got_count }} / {{ row.total_count }}</template>
      </el-table-column>
      <el-table-column label="剩余" width="80">
        <template #default="{ row }">{{ row.remain }}</template>
      </el-table-column>
      <!-- 领了不用等于活动没效果：核销数与核销率才是运营真正要看的口径 -->
      <el-table-column label="已核销" width="120">
        <template #default="{ row }">
          <span :class="row.used_count ? 'used' : 'unused'">{{ row.used_count ?? 0 }} 张</span>
          <span class="rate">{{ useRate(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="start_at" label="开始" width="120" />
      <el-table-column prop="end_at" label="结束" width="120" />
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-switch v-model="row.status" :active-value="1" :inactive-value="0"
            @change="toggle(row)" />
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="show" title="创建优惠券" width="460px">
      <el-form :model="form" label-width="84px">
        <el-form-item label="券名称"><el-input v-model="form.couponName" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="form.couponType">
            <el-radio :label="1">满减券</el-radio>
            <el-radio :label="2">折扣券</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="form.couponType===1">
          <el-form-item label="使用门槛"><el-input-number v-model="form.threshold" :min="0" /></el-form-item>
          <el-form-item label="减免金额"><el-input-number v-model="form.amount" :min="0" /></el-form-item>
        </template>
        <el-form-item v-else label="折扣">
          <el-input-number v-model="form.discount" :min="0.1" :max="0.99" :step="0.1" :precision="2" />
        </el-form-item>
        <el-form-item label="发放总量"><el-input-number v-model="form.totalCount" :min="1" /></el-form-item>
        <el-form-item label="有效期">
          <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD"
            start-placeholder="开始" end-placeholder="结束" style="width:100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="show=false">取消</el-button>
        <el-button type="primary" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  couponsAdminApi, createCouponApi, setCouponStatusApi, type CouponForm, type CouponTemplate,
} from '@/api/marketing'
const list = ref<CouponTemplate[]>([])
const loading = ref(true)
const show = ref(false)
const range = ref<string[]>([])
const form = reactive<any>({ couponName: '', couponType: 1, threshold: 20, amount: 5, discount: 0.9, totalCount: 100 })

/** 核销率按「已核销 / 已领取」算，分母用领取量：发放 100 张只领走 3 张时，
 *  用发放量当分母会把一场没人领的活动读成"效果差"，实际是"没触达"。 */
function useRate(row: CouponTemplate) {
  if (!row.got_count) return '—'
  return `${Math.round(((row.used_count ?? 0) / row.got_count) * 100)}%`
}
async function load() {
  loading.value = true
  try { list.value = await couponsAdminApi() } finally { loading.value = false }
}
async function submit() {
  if (!form.couponName) return ElMessage.warning('请填写券名称')
  if (!range.value?.length) return ElMessage.warning('请选择有效期')
  const payload: CouponForm = {
    ...form,
    amount: form.couponType === 1 ? form.amount : undefined,
    discount: form.couponType === 2 ? form.discount : undefined,
    startAt: range.value[0],
    endAt: range.value[1],
  }
  await createCouponApi(payload)
  ElMessage.success('优惠券已创建'); show.value = false; load()
}
async function toggle(row: CouponTemplate) {
  await setCouponStatusApi(row.id, row.status)
  ElMessage.success(row.status===1?'已上架':'已下架')
}
onMounted(load)
</script>

<style scoped>
.used { color: var(--ts-primary-dark); font-weight: 700; }
.unused { color: var(--ts-text-secondary); }
.rate { margin-left: 6px; font-size: 12px; color: var(--ts-text-secondary); }
</style>
