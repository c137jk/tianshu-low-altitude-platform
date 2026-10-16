<template>
  <el-card v-loading="loading" shadow="never">
    <template #header><b>运行告警</b></template>
    <el-table :data="list" stripe>
      <el-table-column label="级别" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.level === '严重' ? 'danger' : 'warning'">{{ row.level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="type" label="类型" width="130" />
      <el-table-column prop="target" label="对象" width="140" />
      <el-table-column prop="detail" label="详情" min-width="260" />
    </el-table>
  </el-card>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import request from '@/utils/request'
const list = ref<any[]>([])
const loading = ref(true)
onMounted(async () => {
  try { list.value = (await request('/monitor/alerts')).list } finally { loading.value = false }
})
</script>
