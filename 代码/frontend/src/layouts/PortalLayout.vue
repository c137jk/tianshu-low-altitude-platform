<template>
  <div class="portal-layout">
    <header class="portal-header">
      <div class="brand">
        <span class="brand-mark">天</span>
        <span class="brand-name">天枢低空物流 · 客户中心</span>
      </div>
      <nav class="nav">
        <router-link
          v-for="item in menus"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ disabled: item.disabled }"
        >{{ item.label }}</router-link>
      </nav>
      <div class="user-box">
        <span>{{ userStore.userInfo?.realName || userStore.userInfo?.username }}</span>
        <el-button link type="primary" @click="onLogout">退出</el-button>
      </div>
    </header>
    <main class="portal-main">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { resetDynamicRoutes } from '@/router'

const router = useRouter()
const userStore = useUserStore()

const menus: { label: string; path: string; disabled?: boolean }[] = [
  { label: '首页', path: '/portal/home' },
  { label: '我要下单', path: '/portal/order/create' },
  { label: '我的订单', path: '/portal/orders' },
  { label: '我的账单', path: '/portal/bills' },
  { label: '常用地址', path: '/portal/addresses' },
  { label: '领券中心', path: '/portal/coupons' },
  { label: '商家入驻', path: '/portal/merchant-apply' },
  { label: '消息通知', path: '/portal/messages' },
  { label: '评价投诉', path: '/portal/feedback' },
  { label: '个人中心', path: '/portal/profile' },
]

async function onLogout() {
  await userStore.logout()
  resetDynamicRoutes()
  router.replace('/login')
}
</script>

<style lang="scss" scoped>
.portal-layout { min-height: 100vh; background: var(--ts-bg); }
.portal-header {
  min-height: 64px; background: #fff; border-bottom: 1px solid var(--ts-border);
  display: flex; align-items: center; padding: 0 32px; gap: 32px;
  position: sticky; top: 0; z-index: 100;
}
.brand { display: flex; align-items: center; gap: 10px; }
.brand-mark {
  width: 32px; height: 32px; border-radius: 4px;
  background: var(--ts-primary); color: var(--ts-gold-light);
  display: flex; align-items: center; justify-content: center; font-weight: 700;
}
.brand-name { font-weight: 700; color: var(--ts-primary-dark); font-size: 16px; white-space: nowrap; }
.nav { display: flex; gap: 22px; flex: 1; flex-wrap: wrap; }
.nav-item {
  color: var(--ts-text-regular); cursor: pointer; font-size: 14px;
  text-decoration: none; line-height: 64px; position: relative;
}
.nav-item:hover { color: var(--ts-primary); }
.nav-item.router-link-exact-active {
  color: var(--ts-primary); font-weight: 600;
}
.nav-item.router-link-exact-active::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: 0;
  height: 3px; background: var(--ts-gold);
}
.user-box { display: flex; align-items: center; gap: 12px; font-size: 13px; color: var(--ts-text); white-space: nowrap; }
.portal-main { padding: 24px 32px; max-width: 1280px; margin: 0 auto; width: 100%; box-sizing: border-box; }
</style>
