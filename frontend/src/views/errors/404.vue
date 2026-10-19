<template>
  <div class="err-page">
    <div class="code">404</div>
    <p>页面不存在或尚未开放</p>
    <el-button type="primary" @click="$router.replace('/')">返回首页</el-button>
  </div>
</template>

<style scoped>
.err-page { height: 80vh; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; }
.code { font-size: 72px; font-weight: 700; color: #23406e; }
p { color: #5f6670; }
</style>
