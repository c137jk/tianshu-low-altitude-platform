/** 登录态：token / 用户信息 / 后端权限菜单树 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  loginApi, getMe, getMenus, logoutApi,
  type LoginPayload, type UserInfo, type MenuNode,
} from '@/api/auth'
import { TOKEN_KEY, setTokens, clearTokens } from '@/utils/request'
import { staffHome } from '@/constants/menu'

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) || '')
  const userInfo = ref<UserInfo | null>(null)
  const menus = ref<MenuNode[]>([])
  const loaded = ref(false) // 刷新页面后是否已重新拉过用户信息

  const perms = computed<string[]>(() => userInfo.value?.perms ?? [])
  const roles = computed<string[]>(() => userInfo.value?.roles ?? [])

  // 客户身份（1 个人 / 2 企业）：门户路由只给他们挂，员工路由表里不该有 /portal/*
  const isCustomer = computed(() => {
    const t = userInfo.value?.userType
    return t === 1 || t === 2
  })

  // 登录后按角色分流（D-25）：1/2 客户进门户，3/4/5/6 员工/平台进管控台。
  // 员工首页按菜单+权限算：写死一个路径的话，没有该权限的账号（如财务没有实时监控）
  // 会命中 404 兜底路由，且因为兜底路径恰好等于首页，守卫不再重定向，直接给他看 404 页。
  const homePath = computed(() => isCustomer.value ? '/portal/home' : staffHome(menus.value, hasPerm))

  // 平台侧身份（4 平台管理员 / 5 空管 / 6 运维）：和后端 scope.PLATFORM_TYPES 同一口径。
  // 前端用它决定"所属商家"这类只有平台侧能看到的列和写操作按钮露不露。
  const isPlatform = computed(() => {
    const t = userInfo.value?.userType
    return roles.value.includes('platform_admin') || (!!t && t >= 4 && t <= 6)
  })

  function setToken(t: string, refreshToken?: string) {
    token.value = t
    setTokens(t, refreshToken)
  }

  async function login(payload: LoginPayload) {
    const res = await loginApi(payload)
    // refreshToken 一起存：access 过期时 request.ts 拿它静默续期，不把人踢回登录页
    setToken(res.token, res.refreshToken)
    userInfo.value = res.userInfo
    menus.value = res.menus
    loaded.value = true
    return res
  }

  /** 刷新页面后用 token 恢复登录态 */
  async function restore() {
    const { userInfo: me } = await getMe()
    userInfo.value = me
    menus.value = await getMenus()
    loaded.value = true
  }

  function hasPerm(code: string): boolean {
    return roles.value.includes('platform_admin') || perms.value.includes(code)
  }

  /**
   * 只清本地登录态。给"票已经失效"那条路径用：那里不能再走 logout()，
   * 因为 /auth/logout 本身也会吃一个 401，续期再失败又回到这里，绕一圈才落到登录页。
   */
  function dropLocal() {
    token.value = ''
    userInfo.value = null
    menus.value = []
    loaded.value = false
    // 两张票都要清：只清 access 的话，下一次请求会拿残留的 refresh 票悄悄把登录态续回来
    clearTokens()
  }

  async function logout() {
    try { await logoutApi() } catch { /* 本地也要能退出 */ }
    dropLocal()
  }

  return {
    token, userInfo, menus, loaded, perms, roles, homePath, isCustomer, isPlatform,
    login, restore, logout, dropLocal, hasPerm,
  }
})
