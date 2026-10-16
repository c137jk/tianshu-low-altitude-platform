<template>
  <el-card shadow="never">
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>客服工单</b>
        <el-button type="primary" size="small" @click="show=true">新建工单</el-button>
      </div>
    </template>
    <el-table :data="list" stripe v-loading="loading">
      <el-table-column prop="ticket_no" label="工单号" width="190" />
      <el-table-column prop="title" label="标题" width="200" />
      <el-table-column prop="content" label="内容" />
      <el-table-column label="优先级" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.priority>=3?'danger':row.priority===2?'warning':'info'">
            {{ priName[row.priority] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="stType[row.status]">{{ stName[row.status] }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180">
        <template #default="{ row }">
          <el-button v-if="row.status===1" link type="primary" size="small" @click="take(row)">接单</el-button>
          <el-button v-if="row.status===2" link type="success" size="small" @click="resolve(row)">解决并关闭</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="show" title="新建客服工单" width="460px">
      <el-form :model="form" label-width="72px">
        <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="内容"><el-input v-model="form.content" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="优先级">
          <el-radio-group v-model="form.priority">
            <el-radio :label="1">低</el-radio>
            <el-radio :label="2">中</el-radio>
            <el-radio :label="3">高</el-radio>
          </el-radio-group>
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
import request from '@/utils/request'
const list = ref<any[]>([])
const loading = ref(true)
const show = ref(false)
const form = reactive<any>({ title: '', content: '', priority: 2 })
const priName: Record<number, string> = { 1: '低', 2: '中', 3: '高' }
const stName: Record<number, string> = { 1: '待受理', 2: '处理中', 3: '待确认', 4: '已关闭' }
const stType: Record<number, '' | 'warning' | 'success' | 'info'> = {
  1: 'info', 2: 'warning', 3: '', 4: 'success' }
async function load() {
  loading.value = true
  try { list.value = await request('/aux/work-orders') } finally { loading.value = false }
}
async function submit() {
  if (!form.title) return ElMessage.warning('请填写标题')
  await request('/aux/work-orders', { method: 'POST', data: { ...form } })
  ElMessage.success('工单已创建'); show.value = false; load()
}
async function take(row: any) {
  await request(`/aux/work-orders/${row.id}`, { method: 'PUT', data: { status: 2 } })
  ElMessage.success('已接单'); load()
}
async function resolve(row: any) {
  await request(`/aux/work-orders/${row.id}`, { method: 'PUT', data: { status: 4 } })
  ElMessage.success('工单已关闭'); load()
}
onMounted(load)
</script>
