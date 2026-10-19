<template>
  <el-card v-loading="loading" shadow="never">
    <template #header><b>机长资质台账</b></template>
    <el-table :data="list" stripe>
      <el-table-column prop="pilotName" label="机长" width="110" />
      <el-table-column prop="qualName" label="资质名称" min-width="220" />
      <el-table-column prop="issueAt" label="签发" width="100" />
      <el-table-column prop="expireAt" label="到期日" width="120" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 1 ? 'success' : row.status === 3 ? 'danger' : 'warning'">
            {{ row.statusName }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listQualificationsApi, type QualVO } from '@/api/resource'
const list = ref<QualVO[]>([])
const loading = ref(true)
onMounted(async () => {
  try { list.value = (await listQualificationsApi()).list } finally { loading.value = false }
})
</script>
