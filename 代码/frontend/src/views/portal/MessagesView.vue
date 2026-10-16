<template>
  <el-card shadow="never">
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>消息通知 <el-badge v-if="unread" :value="unread" /></b>
        <el-button size="small" @click="readAll">全部已读</el-button>
      </div>
    </template>
    <el-table :data="list" stripe v-loading="loading">
      <el-table-column label="" width="40">
        <template #default="{ row }">
          <el-icon v-if="!row.is_read" color="#c8a45c"><BellFilled /></el-icon>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="标题" width="220" />
      <el-table-column prop="content" label="内容" />
      <el-table-column prop="created_at" label="时间" width="170" />
    </el-table>
  </el-card>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import request from '@/utils/request'
const list = ref<any[]>([])
const unread = ref(0)
const loading = ref(true)
async function load() {
  loading.value = true
  try {
    const d = await request('/aux/messages')
    list.value = d.list; unread.value = d.unread
  } finally { loading.value = false }
}
async function readAll() {
  await request('/aux/messages/read-all', { method: 'POST' })
  load()
}
onMounted(load)
</script>
