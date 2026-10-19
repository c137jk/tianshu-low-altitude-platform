<template>
  <el-container class="admin-layout">
    <!-- 左侧深色菜单（D-28 藏青 + 金色选中） -->
    <el-aside width="224px" class="aside">
      <div class="logo">
        <span class="logo-mark">天</span>
        <div class="logo-text">
          <div class="logo-title">天枢管控台</div>
          <div class="logo-sub">TIANSHU UTM</div>
        </div>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="false"
        class="side-menu"
        background-color="transparent"
        text-color="#B9C6DC"
        active-text-color="#E0C993"
        router
      >
        <template v-for="m in nav" :key="m.permCode">
          <!-- 只有一个可进页面的模块直接平铺，多点一次没意义 -->
          <el-menu-item v-if="m.children.length <= 1" :index="m.children[0]?.path || m.landing">
            <el-icon><component :is="iconMap[m.permCode] || 'Menu'" /></el-icon>
            <span>{{ m.label }}</span>
          </el-menu-item>
          <el-sub-menu v-else :index="`g-${m.permCode}`">
            <template #title>
              <el-icon><component :is="iconMap[m.permCode] || 'Menu'" /></el-icon>
              <span>{{ m.label }}</span>
            </template>
            <el-menu-item v-for="c in m.children" :key="c.path" :index="c.path">
              {{ c.title }}
            </el-menu-item>
          </el-sub-menu>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="crumb">
          <el-icon><Location /></el-icon>
          <span>{{ currentTitle }}</span>
        </div>
        <div class="header-right">
          <!-- 站内信：机长接单、违规处罚通知书、排班变更都从这里冒出来 -->
          <el-popover v-model:visible="msgOpen" placement="bottom-end" :width="380" trigger="click" @show="loadMsgs">
            <template #reference>
              <el-badge :value="unread" :hidden="!unread" :max="99" class="bell">
                <el-icon :size="18"><Bell /></el-icon>
              </el-badge>
            </template>
            <div class="msg-head">
              <b>消息通知</b>
              <el-button link size="small" :disabled="!unread" @click="readAll">全部已读</el-button>
            </div>
            <div class="msg-list">
              <div v-for="x in msgs" :key="x.id" class="msg-item" :class="{ unread: !x.is_read }"
                @click="openMsg(x)">
                <div class="msg-title">{{ x.title }}</div>
                <div class="msg-body">{{ x.content }}</div>
                <div class="msg-foot">
                  <span class="msg-time">{{ x.created_at }}</span>
                  <span v-if="x.go" class="msg-go">{{ x.go.title }} ›</span>
                  <span v-else class="msg-go dim">仅通知</span>
                </div>
              </div>
              <el-empty v-if="!msgs.length" description="暂无消息" :image-size="60" />
            </div>
          </el-popover>

          <el-dropdown @command="onCommand">
            <span class="user-chip">
              <el-avatar :size="28" class="avatar">{{ avatarText }}</el-avatar>
              <span class="user-name">{{ userStore.userInfo?.realName || userStore.userInfo?.username }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">个人中心</el-dropdown-item>
                <el-dropdown-item command="logout">
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main">
        <router-view v-slot="{ Component }">
          <keep-alive :max="12">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { resetDynamicRoutes } from '@/router'
import { menuLabel, menuLanding, subPages } from '@/constants/menu'
import { msgRoute } from '@/constants/msgRoute'
import request from '@/utils/request'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

// 8 个一级菜单 → Element 图标（DDL 的 icon 列为空，前端按权限编码映射）
const iconMap: Record<string, string> = {
  dashboard: 'Odometer',
  order: 'Tickets',
  dispatch: 'Position',
  airspace: 'MapLocation',
  resource: 'Box',
  monitor: 'Monitor',
  settlement: 'Wallet',
  service: 'Service',
  marketing: 'Discount',
  system: 'Setting',
}

/** 侧边栏：后端菜单树 × 前端权限落点表，只留当前账号真进得去的页面 */
const nav = computed(() => userStore.menus
  .map((m) => {
    const has = (c: string) => userStore.hasPerm(c)
    const landing = menuLanding(m.permCode, has)
    return {
      permCode: m.permCode,
      label: menuLabel(m.permCode, m.permName, has),
      children: subPages(m.permCode, has),
      landing: landing || '/admin/profile',
      reachable: !!landing,
    }
  })
  // 一个页面都进不去的模块直接不显示。以前落点兜底成个人中心，于是空管点「订单管理」
  // （他有 order 这个模块入口权限，却没有 order:list 任何页面权限）被送去个人中心，
  // 表现就是"订单管理页面怎么是个人中心"。进不去的东西不该占菜单位置。
  .filter((m) => m.reachable))

const activeMenu = computed(() => {
  // 详情页（如 /admin/dispatch/plans/12）不在菜单里：高亮它所属模块的第一项
  if (nav.value.some((m) => m.children.some((c) => c.path === route.path))) return route.path
  const seg = route.path.split('/')[2]
  return nav.value.find((m) => m.landing.startsWith(`/admin/${seg}`))?.landing || route.path
})

// ---- 站内信 ----
const msgs = ref<any[]>([])
const unread = ref(0)
const msgOpen = ref(false)
let msgTimer: number | null = null

async function loadMsgs() {
  try {
    const d = await request('/aux/messages')
    // 落点在拉到时就算好：跳之前先确认这个账号真进得去那条路由，
    // 否则 push 会命中 404 兜底、被守卫送回首页，看着就是"点了没反应"
    msgs.value = (d.list || []).map((x: any) => ({
      ...x, go: msgRoute(x.biz_type, x.biz_id, (c) => userStore.hasPerm(c)),
    }))
    unread.value = d.unread
  } catch { /* 铃铛拉不到不能影响页面 */ }
}
async function openMsg(x: any) {
  if (!x.is_read) {
    await request(`/aux/messages/${x.id}/read`, { method: 'POST' })
    x.is_read = 1
    unread.value = Math.max(0, unread.value - 1)
  }
  if (!x.go) return
  msgOpen.value = false
  if (route.fullPath === x.go.path) return
  await router.push(x.go.path)
}
async function readAll() {
  await request('/aux/messages/read-all', { method: 'POST' })
  await loadMsgs()
}

const currentTitle = computed(() => (route.meta.title as string) || '运行驾驶舱')
const avatarText = computed(() => (userStore.userInfo?.realName || userStore.userInfo?.username || 'U').slice(0, 1))

async function onCommand(cmd: string) {
  if (cmd === 'profile') { router.push('/admin/profile'); return }
  if (cmd === 'logout') {
    await ElMessageBox.confirm('确定退出登录吗？', '提示', { type: 'warning' })
    await userStore.logout()
    resetDynamicRoutes()
    router.replace('/login')
  }
}

onMounted(() => {
  loadMsgs()
  // 机长接单、违规通知书都靠它冒出来；30s 一次，不用 WebSocket 也够演示
  msgTimer = window.setInterval(loadMsgs, 30000)
})
onBeforeUnmount(() => { if (msgTimer) clearInterval(msgTimer) })
</script>

<style lang="scss" scoped>
.admin-layout { height: 100vh; }
.aside {
  background: var(--ts-sidebar);
  border-right: 2px solid rgba(200, 164, 92, 0.25);
  overflow-y: auto;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.logo-mark {
  width: 34px; height: 34px;
  display: flex; align-items: center; justify-content: center;
  background: var(--ts-gold); color: #1c3358;
  font-weight: 700; font-size: 18px; border-radius: 4px;
}
.logo-title { color: #f3eedd; font-size: 15px; font-weight: 700; letter-spacing: 1px; }
.logo-sub { color: #8fa0bb; font-size: 9px; letter-spacing: 2px; }
.side-menu { border-right: none; }
.side-menu :deep(.el-menu-item) { height: 46px; margin: 2px 8px; border-radius: 4px; }
.side-menu :deep(.el-sub-menu .el-menu-item) { height: 38px; margin: 2px 8px 2px 18px; font-size: 13px; }
.side-menu :deep(.el-sub-menu__title) { height: 46px; margin: 2px 8px; border-radius: 4px; }
.side-menu :deep(.el-menu-item.is-active) {
  background: rgba(200, 164, 92, 0.14) !important;
  box-shadow: inset 3px 0 0 var(--ts-gold);
}
.header {
  height: 56px;
  background: var(--ts-card);
  border-bottom: 1px solid var(--ts-border);
  display: flex; align-items: center; justify-content: space-between;
}
.crumb { display: flex; align-items: center; gap: 6px; color: var(--ts-text-regular); font-size: 14px; }
.header-right { display: flex; align-items: center; gap: 18px; }
.bell { cursor: pointer; color: var(--ts-text-regular); display: flex; align-items: center; outline: none; }
.bell:hover { color: var(--ts-gold); }
.user-chip { display: flex; align-items: center; gap: 8px; cursor: pointer; outline: none; }
.avatar { background: var(--ts-primary); color: #fff; font-size: 13px; }
.user-name { color: var(--ts-text); font-size: 13px; }
.role-tag { transform: scale(0.9); }
.main { background: var(--ts-bg); padding: 16px; }
</style>

<style lang="scss">
/* 消息气泡挂在 body 下，scoped 选择器够不着 */
.msg-head { display: flex; align-items: center; justify-content: space-between; padding-bottom: 8px;
  border-bottom: 1px solid var(--ts-border); }
.msg-list { max-height: 340px; overflow-y: auto; }
.msg-item { padding: 9px 6px; border-bottom: 1px dashed var(--ts-border); cursor: pointer; }
.msg-item:hover { background: var(--ts-bg); }
.msg-item.unread .msg-title::before { content: ''; display: inline-block; width: 6px; height: 6px;
  border-radius: 50%; background: var(--ts-gold); margin-right: 6px; vertical-align: middle; }
.msg-title { font-size: 13px; font-weight: 600; color: var(--ts-primary-dark); }
.msg-body { font-size: 12px; color: var(--ts-text-regular); margin: 3px 0; line-height: 1.5; }
.msg-foot { display: flex; align-items: center; justify-content: space-between; }
.msg-time { font-size: 11px; color: var(--ts-text-secondary); }
/* 落点名就是"点这条消息会去哪儿"的预告：进不去的页面不写路径，只标"仅通知" */
.msg-go { font-size: 11px; color: var(--ts-primary-dark); }
.msg-go.dim { color: var(--ts-text-secondary); }
</style>
