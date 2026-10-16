/** 一级菜单 → 二级页面 + 落点解析（按权限算，不写死）。
 *
 * 为什么要有这个文件：以前落点是 AdminLayout 里一张写死的 code→path 表，
 * 不看当前账号有没有那个页面的权限。后果是空管点「调度飞行」被送去
 * /admin/dispatch/board（他没有 dispatch:plan，路由压根没注册），守卫兜底又把他弹回首页，
 * 表现就是"点了没反应"；财务的首页写死 /admin/monitor/realtime，同理直接吃到 404 页。
 * 落点、侧边栏二级菜单、router 注册条件三者必须用同一套权限判断，所以都从这里取。
 */
import type { MenuNode } from '@/api/auth'

export type HasPerm = (code: string) => boolean

export interface SubPage {
  title: string
  path: string
  /** 需要单个权限点 */
  perm?: string
  /** 任一命中即可（一个页面承载多类页签时用） */
  perms?: string[]
}

/** 一级菜单编码 → 该模块下的页面清单（顺序即优先级，第一个进得去的就是落点） */
export const MENU_CHILDREN: Record<string, SubPage[]> = {
  monitor: [
    { title: '实时监控', path: '/admin/monitor/realtime', perm: 'monitor:realtime' },
    { title: '轨迹回放', path: '/admin/monitor/track', perm: 'monitor:track' },
    { title: '设备在线', path: '/admin/monitor/devices', perm: 'monitor:device' },
    { title: '运行告警', path: '/admin/monitor/alerts', perm: 'monitor:realtime' },
  ],
  // 需求 4.2.5 统计报表（M-12）落在「运行驾驶舱」模块里：report:view/report:export
  // 在权限表里都挂在 dashboard 菜单下，所以不自建一级模块。
  // 这里必须把驾驶舱自己也列进来：侧边栏对"只有一个子页面"的模块会直接平铺那一个页面，
  // 只写统计报表的话，有报表权限的账号就再也点不进运行驾驶舱了。
  dashboard: [
    { title: '运行总览', path: '/admin/dashboard', perm: 'dashboard' },
    { title: '统计报表', path: '/admin/report', perm: 'report:view' },
  ],
  order: [
    { title: '订单列表', path: '/admin/order/list', perm: 'order:list' },
  ],
  dispatch: [
    { title: '调度工作台', path: '/admin/dispatch/board', perm: 'dispatch:plan' },
    { title: '飞行计划', path: '/admin/dispatch/plans', perms: ['dispatch:plan', 'approval:handle'] },
    { title: '我的任务', path: '/admin/dispatch/missions', perm: 'dispatch:execute' },
  ],
  // 需求 4.2.4 空域合规运营：空域数据同步/一张图、飞行计划申报、智能审批、
  // 空域动态监控（含电子围栏）、违规处理
  airspace: [
    { title: '空域一张图', path: '/admin/airspace/map', perms: ['airspace:draw', 'airspace:audit'] },
    { title: '空域台账与审核', path: '/admin/airspace/list', perms: ['airspace:draw', 'airspace:audit'] },
    { title: '电子围栏', path: '/admin/airspace/fence', perms: ['airspace:draw', 'airspace:audit'] },
    { title: '飞行计划审批', path: '/admin/airspace/approval', perm: 'approval:handle' },
    { title: '违规处理', path: '/admin/airspace/violations', perm: 'monitor:violation' },
  ],
  // 需求 4.2.3 低空资源管理：场站、飞行器（含维保台账）、人员（台账 + 资质复审）、排班（班次表）
  resource: [
    { title: '场站管理', path: '/admin/resource/vertiports', perm: 'resource:vertiport' },
    { title: '飞行器管理', path: '/admin/resource/aircraft', perm: 'resource:aircraft' },
    {
      title: '人员管理', path: '/admin/resource/personnel',
      perms: ['resource:qualification', 'resource:schedule'],
    },
    {
      title: '排班管理', path: '/admin/resource/schedule',
      // resource:schedule 是能排班的完整门；resource:schedule:view 是只给"看班表"的岗位
      // （空管、财务）——同一个页面，进去后新增/删除按权限隐藏，页头标「只读」。
      perms: ['resource:schedule', 'resource:schedule:view'],
    },
  ],
  // 需求 4.2.6 结算对账：账单查询（M-71/72）+ 支付流水（M-73）+ 发票（M-74，在账单页里走申请→开具）
  settlement: [
    { title: '账单查询', path: '/admin/settlement/bills', perm: 'settlement:list' },
    { title: '支付流水', path: '/admin/settlement/payments', perm: 'settlement:list' },
  ],
  marketing: [
    { title: '营销中心', path: '/admin/marketing/center', perm: 'marketing:coupon' },
  ],
  service: [
    { title: '客服中心', path: '/admin/service/center', perm: 'service:workorder' },
  ],
  system: [{
    title: '系统管理', path: '/admin/system/users',
    perms: ['system:user', 'system:role', 'system:config', 'system:log', 'system:article',
      'demo:run', 'merchant:audit'],
  }],
}

function allowed(p: SubPage, has: HasPerm): boolean {
  if (p.perm && !has(p.perm)) return false
  if (p.perms && !p.perms.some(has)) return false
  return true
}

/** 当前账号在该模块下进得去的页面 */
export function subPages(permCode: string, has: HasPerm): SubPage[] {
  return (MENU_CHILDREN[permCode] || []).filter((p) => allowed(p, has))
}

/** 模块落点：第一个进得去的页面；一个都没有返回 undefined（菜单挂占位页） */
export function menuLanding(permCode: string, has: HasPerm): string | undefined {
  if (permCode === 'dashboard') return '/admin/dashboard'
  return subPages(permCode, has)[0]?.path
}

/** 菜单里第一个进得去的页面；一个都没有就退回个人中心（该页不需要权限） */
export function firstLanding(menus: MenuNode[], has: HasPerm): string {
  for (const m of menus) {
    const p = menuLanding(m.permCode, has)
    if (p) return p
  }
  return '/admin/profile'
}

/** 员工登录后的首页。
 *
 * 机长优先落任务台：他不关心全区态势，进来第一件事是看有没有新任务；
 * 其余员工保持原来的「实时监控」，没有实时监控权限的（如财务）退到运行驾驶舱。
 */
export function staffHome(menus: MenuNode[], has: HasPerm): string {
  if (has('dispatch:execute') && !has('dispatch:plan')) return '/admin/dispatch/missions'
  if (has('monitor:realtime')) return '/admin/monitor/realtime'
  if (menus.some((m) => m.permCode === 'dashboard')) return '/admin/dashboard'
  return firstLanding(menus, has)
}

/** 菜单显示名。机长的「调度飞行」里只有他自己的任务台，沿用原名会误导。 */
export function menuLabel(permCode: string, permName: string, has: HasPerm): string {
  if (permCode === 'dispatch' && !has('dispatch:plan') && has('dispatch:execute')) return '我的任务'
  return permName
}
