<template>
  <div class="ts-card placeholder">
    <el-icon class="ph-icon"><Tools /></el-icon>
    <h2>{{ title }}</h2>
    <p>该模块页面按六批开发顺序建设中（见设计06/设计08），后端接口与权限点已就绪。</p>
    <el-tag type="info" effect="plain">W2 起逐批交付</el-tag>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Tools } from '@element-plus/icons-vue'
const route = useRoute()
const title = computed(() => (route.meta.title as string) || '模块')
</script>

<style lang="scss" scoped>
.placeholder { text-align: center; padding: 80px 20px; }
.ph-icon { font-size: 44px; color: var(--ts-gold); }
h2 { color: var(--ts-primary-dark); margin: 16px 0 8px; }
p { color: var(--ts-text-secondary); font-size: 13px; }
</style>
