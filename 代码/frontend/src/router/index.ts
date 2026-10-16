import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { menuLanding } from '@/constants/menu'
import type { MenuNode } from '@/api/auth'

// ---- 固定路由（不依赖权限）----
export const constantRoutes: RouteRecordRaw[] = [
  { path: '/', redirect: '/login' },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/index.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/403',
    component: () => import('@/views/errors/403.vue'),
    meta: { title: '无权限', public: true },
  },
  {
    // 深蓝黑监控大屏：独立全屏，不登录、不走后台外壳
    path: '/screen',
    component: () => import('@/views/screen/MonitorScreen.vue'),
    meta: { title: '低空态势监控大屏', public: true },
  },
  {
    // 管控台外壳；内部页面按后端菜单动态 addRoute。
    // 落点用函数取 homePath：写死一个路径的话，没有该权限的账号会一路吃到 404 页。
    path: '/admin',
    name: 'admin',
    component: () => import('@/layouts/AdminLayout.vue'),
    redirect: () => useUserStore().homePath,
    children: [],
  },
  // 404 兜底在 buildDynamicRoutes 末尾注册（必须最后注册，否则会吞掉后加的动态路由）
]

/**
 * 客户门户外壳（C-1~C-8）：整棵子树只在客户（user_type 1/2）登录后才挂。
 * 员工的路由表里根本没有 /portal/*，敲进来落进 404 兜底、由守卫送回自己的首页，
 * 而不是把门户组件渲染起来再让页内接口一份份报 403。
 */
const portalRoutes: RouteRecordRaw = {
  path: '/portal',
  component: () => import('@/layouts/PortalLayout.vue'),
  redirect: '/portal/home',
  children: [
    { path: 'home', component: () => import('@/views/portal/HomeView.vue'), meta: { title: '客户首页' } },
    { path: 'order/create', component: () => import('@/views/portal/OrderCreate.vue'), meta: { title: '在线下单' } },
    { path: 'orders', component: () => import('@/views/portal/OrderList.vue'), meta: { title: '我的订单' } },
    { path: 'orders/:id', component: () => import('@/views/portal/OrderDetail.vue'), meta: { title: '订单详情' } },
    { path: 'bills', component: () => import('@/views/portal/BillsView.vue'), meta: { title: '我的账单' } },
    { path: 'profile', component: () => import('@/views/portal/ProfileView.vue'), meta: { title: '个人中心' } },
    { path: 'addresses', component: () => import('@/views/portal/AddressBook.vue'), meta: { title: '常用地址' } },
    { path: 'coupons', component: () => import('@/views/portal/Coupons.vue'), meta: { title: '领券中心' } },
    { path: 'merchant-apply', component: () => import('@/views/portal/MerchantApply.vue'), meta: { title: '商家入驻' } },
    { path: 'messages', component: () => import('@/views/portal/MessagesView.vue'), meta: { title: '消息通知' } },
    { path: 'feedback', component: () => import('@/views/portal/FeedbackView.vue'), meta: { title: '评价投诉' } },
  ],
}

const router = createRouter({
  history: createWebHistory(),
  routes: constantRoutes,
})

let dynamicBuilt = false

/**
 * 按后端权限菜单动态挂管控台路由（设计06：菜单+动作点驱动）。
 * 一级菜单壳来自 menus，落点由 constants/menu.ts 按权限算（和侧边栏共用同一张表）；
 * 具体页面按"权限点→页面"注册，没有权限的页面根本不进路由表
 * （直接敲地址也会被守卫送回首页）。
 */
export function buildDynamicRoutes(menus: MenuNode[]) {
  if (dynamicBuilt) return
  const userStore = useUserStore()
  const has = (code: string) => userStore.hasPerm(code)

  const dashboard = () => import('@/views/admin/DashboardView.vue')
  const placeholder = () => import('@/views/admin/PlaceholderView.vue')

  if (userStore.isCustomer) router.addRoute(portalRoutes)

  menus.forEach((m) => {
    const seg = (m.path || '').replace(/^\//, '')
    if (!seg) return
    if (m.permCode === 'dashboard') {
      router.addRoute('admin', {
        path: 'dashboard',
        component: dashboard,
        meta: { title: m.permName, perm: 'dashboard' },
      })
      return
    }
    const landing = menuLanding(m.permCode, has)
    if (landing) {
      router.addRoute('admin', { path: seg, redirect: landing, meta: { title: m.permName } })
    } else {
      // 菜单下发了但一个落地页都进不去（授权被改窄）：给占位页而不是让人吃 404
      router.addRoute('admin', {
        path: seg, component: placeholder,
        meta: { title: m.permName, perm: m.permCode },
      })
    }
  })

  router.addRoute('admin', {
    path: 'demo',
    component: () => import('@/views/admin/DemoView.vue'),
    meta: { title: '一键演示', perm: 'demo:run' },
  })
  router.addRoute('admin', { path: 'profile', component: () => import('@/views/admin/Profile.vue') })

  // M-12 统计报表中心：区间报表 + 图表 + 导出。进门要 report:view，
  // 页内「导出 CSV」再单独判 report:export（看数和把整份经营数据带走是两件事）
  if (has('report:view')) {
    router.addRoute('admin', {
      path: 'report',
      name: 'report-center',
      component: () => import('@/views/admin/report/ReportCenter.vue'),
      meta: { title: '统计报表中心', perm: 'report:view' },
    })
  }

  // M-21/22 订单管理
  if (has('order:list')) {
    router.addRoute('admin', {
      path: 'order/list',
      name: 'order-list',
      component: () => import('@/views/admin/order/OrderList.vue'),
      meta: { title: '订单列表', perm: 'order:list' },
    })
    router.addRoute('admin', {
      path: 'order/:id',
      name: 'order-detail',
      component: () => import('@/views/admin/order/OrderDetail.vue'),
      meta: { title: '订单详情', perm: 'order:list' },
    })
  }

  // M-31/32/33 调度工作台 / 计划列表 / 计划详情
  if (has('dispatch:plan')) {
    router.addRoute('admin', {
      path: 'dispatch/board',
      name: 'dispatch-board',
      component: () => import('@/views/admin/dispatch/DispatchBoard.vue'),
      meta: { title: '调度工作台', perm: 'dispatch:plan' },
    })
  }
  if (has('dispatch:plan') || has('approval:handle')) {
    router.addRoute('admin', {
      path: 'dispatch/plans',
      name: 'dispatch-plans',
      component: () => import('@/views/admin/dispatch/PlanList.vue'),
      meta: { title: '飞行计划', perms: ['dispatch:plan', 'approval:handle'] },
    })
  }
  if (has('dispatch:plan') || has('approval:handle') || has('dispatch:execute')) {
    router.addRoute('admin', {
      path: 'dispatch/plans/:id',
      name: 'dispatch-plan-detail',
      component: () => import('@/views/admin/dispatch/PlanDetail.vue'),
      meta: { title: '计划详情' }, // 列表/审批/机长三类角色都可能进，内部按权限显隐按钮
    })
  }

  // M-34 空管审批台：需求 4.2.4 把「飞行计划申报/智能审批」划给空域合规，挂在 airspace 下
  if (has('approval:handle')) {
    router.addRoute('admin', {
      path: 'airspace/approval',
      name: 'airspace-approval',
      component: () => import('@/views/admin/dispatch/ApprovalBoard.vue'),
      meta: { title: '飞行计划审批', perm: 'approval:handle' },
    })
  }

  // M-35 机长任务台
  if (has('dispatch:execute')) {
    router.addRoute('admin', {
      path: 'dispatch/missions',
      name: 'dispatch-missions',
      component: () => import('@/views/admin/dispatch/MissionBoard.vue'),
      meta: { title: '我的飞行任务', perm: 'dispatch:execute' },
    })
  }

  // M-41/42/43 空域合规：一张图（绘制/图层）、台账（启停/审核留痕）、电子围栏
  if (has('airspace:draw') || has('airspace:audit')) {
    router.addRoute('admin', {
      path: 'airspace/map',
      name: 'airspace-map',
      component: () => import('@/views/admin/airspace/AirspaceMap.vue'),
      meta: { title: '空域一张图', perms: ['airspace:draw', 'airspace:audit'] },
    })
    router.addRoute('admin', {
      path: 'airspace/list',
      name: 'airspace-list',
      component: () => import('@/views/admin/airspace/AirspaceList.vue'),
      meta: { title: '空域台账与审核', perms: ['airspace:draw', 'airspace:audit'] },
    })
    router.addRoute('admin', {
      path: 'airspace/fence',
      name: 'airspace-fence',
      component: () => import('@/views/admin/airspace/Geofence.vue'),
      meta: { title: '电子围栏', perms: ['airspace:draw', 'airspace:audit'] },
    })
  }
  // M-44 违规处理：需求 4.2.4 归空域合规（监控中心页签保留同一组件）
  router.addRoute('admin', {
    path: 'airspace/violations',
    name: 'airspace-violations',
    component: () => import('@/views/admin/airspace/Violations.vue'),
    meta: { title: '违规处理', perm: 'monitor:violation' },
  })

  // M-5 低空资源：场站 / 飞行器 / 人员（资质+排班+任务统计）
  if (has('resource:vertiport')) {
    router.addRoute('admin', {
      path: 'resource/vertiports',
      name: 'resource-vertiports',
      component: () => import('@/views/admin/resource/Vertiports.vue'),
      meta: { title: '场站管理', perm: 'resource:vertiport' },
    })
  }
  if (has('resource:aircraft')) {
    router.addRoute('admin', {
      path: 'resource/aircraft',
      name: 'resource-aircraft',
      component: () => import('@/views/admin/resource/Aircraft.vue'),
      meta: { title: '飞行器管理', perm: 'resource:aircraft' },
    })
  }
  if (has('resource:qualification') || has('resource:schedule')) {
    router.addRoute('admin', {
      path: 'resource/personnel',
      name: 'resource-personnel',
      component: () => import('@/views/admin/resource/Personnel.vue'),
      meta: { title: '人员管理', perms: ['resource:qualification', 'resource:schedule'] },
    })
    router.addRoute('admin', {
      path: 'resource/qualifications',
      redirect: '/admin/resource/personnel',
    })
  }
  // 排班：管理的岗（运维/商家管理员/调度员/设备运维员）与只读的岗（空管、财务）进同一个页面，
  // 页面内部按 resource:schedule 显隐新增与删除；这里放开的是"进得去"，不是"改得动"。
  if (has('resource:schedule') || has('resource:schedule:view')) {
    router.addRoute('admin', {
      path: 'resource/schedule',
      name: 'resource-schedule',
      // 独立的排班页：以前这条路由和 personnel 指向同一个组件，两条菜单打开的是同一页，
      // 看着就是"人员管理和排班管理没做区分"
      component: () => import('@/views/admin/resource/Schedule.vue'),
      meta: { title: '排班管理', perms: ['resource:schedule', 'resource:schedule:view'] },
    })
  }

  // M-6 监控中心
  if (has('monitor:realtime')) {
    router.addRoute('admin', {
      path: 'monitor/realtime',
      name: 'monitor-realtime',
      component: () => import('@/views/admin/monitor/MonitorCenter.vue'),
      meta: { title: '实时监控', perm: 'monitor:realtime' },
    })
  }
  if (has('monitor:track')) {
    router.addRoute('admin', {
      path: 'monitor/track',
      name: 'monitor-track',
      component: () => import('@/views/admin/monitor/Track.vue'),
      meta: { title: '轨迹回放', perm: 'monitor:track' },
    })
  }
  // M-63 设备在线：运维/空管岗看机载终端与地面设备的心跳与失联
  if (has('monitor:device')) {
    router.addRoute('admin', {
      path: 'monitor/devices',
      name: 'monitor-devices',
      component: () => import('@/views/admin/monitor/Devices.vue'),
      meta: { title: '设备在线', perm: 'monitor:device' },
    })
  }

  // M-7 结算管理
  if (has('settlement:list')) {
    router.addRoute('admin', {
      path: 'settlement/bills',
      name: 'settlement-bills',
      component: () => import('@/views/admin/settlement/Bills.vue'),
      meta: { title: '账单查询', perm: 'settlement:list' },
    })
    // M-73 支付流水：财务核"这笔账单到底收到没有"要看流水，客户侧的 /bills/payments 只查本人
    router.addRoute('admin', {
      path: 'settlement/payments',
      name: 'settlement-payments',
      component: () => import('@/views/admin/settlement/Payments.vue'),
      meta: { title: '支付流水', perm: 'settlement:list' },
    })
  }

  router.addRoute('admin', {
    path: 'monitor/alerts',
    name: 'monitor-alerts',
    component: () => import('@/views/admin/monitor/Alerts.vue'),
    meta: { title: '运行告警', perm: 'monitor:realtime' },
  })
  // 系统中心一个组件承载账号/角色/参数/日志/公告/仿真六类页签，
  // 进门条件放宽为"任一 system:* 或 demo:run"，页签内部再按权限逐个显隐
  router.addRoute('admin', {
    path: 'system/users',
    name: 'system-users',
    component: () => import('@/views/admin/system/SystemCenter.vue'),
    meta: {
      title: '用户管理',
      perms: ['system:user', 'system:role', 'system:config', 'system:log',
        'system:article', 'demo:run', 'merchant:audit'],
    },
  })

  // 营销中心 / 客服中心（菜单由 permission 表下发）
  router.addRoute('admin', {
    path: 'marketing/center',
    name: 'marketing-center',
    component: () => import('@/views/admin/marketing/MarketingCenter.vue'),
    meta: { title: '营销中心', perm: 'marketing:coupon' },
  })
  router.addRoute('admin', {
    path: 'service/center',
    name: 'service-center',
    component: () => import('@/views/admin/service/ServiceCenter.vue'),
    meta: { title: '客服中心', perm: 'service:workorder' },
  })

  // 404 必须最后注册，否则会吞掉后加的动态路由；不加 public：
  // 未登录访问未知地址去登录页，已登录访问权限外地址由守卫送回自己的首页
  router.addRoute({
    path: '/:pathMatch(.*)*',
    component: () => import('@/views/errors/404.vue'),
    meta: { title: '页面不存在' },
  })
  dynamicBuilt = true
}

export function resetDynamicRoutes() {
  // 登出后标记重建（路由表在整页跳登录时自然重置，这里只复位标志）
  dynamicBuilt = false
}

async function ensureLoaded(store: ReturnType<typeof useUserStore>): Promise<boolean> {
  if (store.loaded) return true
  try {
    await store.restore()
  } catch {
    // restore 失败时 request 层已经清掉 localStorage 那两张票，这里把内存里那张也丢掉。
    // 不接住这个 reject 的话，守卫的初始导航悬在半路、动态路由还没挂上，
    // 整页只剩一个空 <div id="app">——过期 token 的用户看到的就是白屏。
    store.dropLocal()
    return false
  }
  buildDynamicRoutes(store.menus)
  return true
}

router.beforeEach(async (to) => {
  const userStore = useUserStore()

  if (to.meta.public) {
    if (to.path === '/login' && userStore.token) {
      // 有票就先把登录态恢复出来：恢复得动才跳首页，恢复不动就留在登录页让人重新登录
      return (await ensureLoaded(userStore)) ? userStore.homePath : true
    }
    return true
  }
  if (!userStore.token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  const coldStart = !userStore.loaded
  if (!(await ensureLoaded(userStore))) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (coldStart) {
    // 冷启动时路由表还没挂上、菜单也是空的，落点只能算出兜底值（个人中心）；
    // 权限菜单到手后重算一次首页，别把人留在兜底页
    const from = to.redirectedFrom?.path
    if (from === '/' || from === '/admin' || from === '/login') return userStore.homePath
    return to.fullPath // 重新进入以匹配刚挂上的动态路由
  }
  if (to.meta.perm && !userStore.hasPerm(to.meta.perm as string)) {
    return '/403'
  }
  // perms：任一命中即可（一个组件承载多类页签时用，如系统中心）
  const anyOf = (to.meta.perms as string[] | undefined) || []
  if (anyOf.length && !anyOf.some((p) => userStore.hasPerm(p))) {
    return '/403'
  }
  // 命中 404 兜底（如客户访问管控台地址、权限外路径）：回到自己的首页而不是看 404
  const isCatchAll = to.matched.some((r) => r.path === '/:pathMatch(.*)*')
  if (isCatchAll && to.fullPath !== userStore.homePath) {
    return userStore.homePath
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${to.meta.title} · 天枢低空运营管控`
    : '天枢·低空智能运营管控平台'
})

export default router
