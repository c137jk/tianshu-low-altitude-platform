<template>
  <el-card shadow="never">
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>评价 · 投诉 · 异常反馈</b>
        <el-button type="primary" size="small" @click="show=true">我要反馈</el-button>
      </div>
    </template>
    <el-table :data="list" stripe v-loading="loading">
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.type===1?'success':row.type===2?'danger':'warning'">
            {{ row.type===1?'评价':row.type===2?'投诉':'异常' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="order_id" label="关联订单" width="100" />
      <el-table-column label="评分" width="160">
        <template #default="{ row }">
          <el-rate v-if="row.score" :model-value="row.score" disabled />
        </template>
      </el-table-column>
      <el-table-column prop="content" label="内容" />
      <el-table-column prop="reply" label="商家回复" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status===2?'info':'primary'">
            {{ row.status===2?'已回复':'处理中' }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="show" title="提交反馈" width="480px">
      <el-form :model="form" label-width="84px">
        <el-form-item label="反馈类型">
          <el-radio-group v-model="form.type">
            <el-radio :label="1">评价</el-radio>
            <el-radio :label="2">投诉</el-radio>
            <el-radio :label="3">异常反馈</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.type===1" label="评分">
          <el-rate v-model="form.score" />
        </el-form-item>
        <el-form-item label="关联订单号">
          <el-input v-model="form.orderId" placeholder="选填，填订单数字ID" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="form.content" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="show=false">取消</el-button>
        <el-button type="primary" @click="submit">提交</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'
const list = ref<any[]>([])
const loading = ref(true)
const show = ref(false)
const form = reactive<any>({ type: 1, score: 5, orderId: '', content: '' })
async function load() {
  loading.value = true
  try { list.value = await request('/aux/feedback') } finally { loading.value = false }
}
async function submit() {
  await request('/aux/feedback', { method: 'POST', data: { ...form } })
  ElMessage.success('已提交'); show.value = false
  Object.assign(form, { type: 1, score: 5, orderId: '', content: '' })
  load()
}
onMounted(load)
</script>
