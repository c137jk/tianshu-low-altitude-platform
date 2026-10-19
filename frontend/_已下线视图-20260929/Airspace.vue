<template>
  <el-card v-loading="loading" shadow="never">
    <template #header><b>空域合规态势</b></template>
    <el-row :gutter="12">
      <el-col :span="14"><div ref="mapEl" class="map" /></el-col>
      <el-col :span="10">
        <el-table :data="list" stripe size="small">
          <el-table-column prop="airspaceName" label="空域名称" min-width="150" />
          <el-table-column label="类型" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="row.airspaceType === 1 ? 'danger' : row.airspaceType === 2 ? 'warning' : 'success'">{{ row.typeName }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="controlReason" label="管制原因" min-width="160" />
        </el-table>
      </el-col>
    </el-row>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listAirspacesApi, type AirspaceVO } from '@/api/dispatch'
import { loadAmap } from '@/composables/useAmap'
const list = ref<AirspaceVO[]>([])
const loading = ref(true)
const mapEl = ref<HTMLElement | null>(null)
onMounted(async () => {
  try {
    const r = await listAirspacesApi()
    list.value = r.list
    const AMap = await loadAmap()
    const map = new AMap.Map(mapEl.value, { zoom: 12.5, center: [120.69, 27.97] })
    for (const a of list.value) {
      const g: any = a.geometry
      if (g?.type === 'Polygon') {
        const color = a.airspaceType === 1 ? '#ff5b5b' : a.airspaceType === 2 ? '#e8c14f' : '#37d67a'
        new AMap.Polygon({
          path: g.coordinates[0].map((c: number[]) => [c[0], c[1]]), map,
          strokeColor: color, fillColor: color, fillOpacity: 0.15, strokeWeight: 2,
        })
      }
    }
  } finally { loading.value = false }
})
</script>
<style scoped>.map { height: 480px; border-radius: 4px; }</style>
