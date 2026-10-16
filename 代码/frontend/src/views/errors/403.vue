<template>
  <div class="err-page">
    <div class="code">403</div>
    <p>抱歉，你的账号没有访问该页面的权限</p>
    <el-button type="primary" @click="$router.replace('/')">返回首页</el-button>
  </div>
</template>

<style scoped>
.err-page { height: 80vh; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; }
.code { font-size: 72px; font-weight: 700; color: #c8a45c; }
p { color: #5f6670; }
</style>
