/** D1 认证领域接口（对应后端 /api/auth/*） */
import request from '@/utils/request'

export interface LoginPayload {
  username: string
  password: string
}

export interface MenuNode {
  id: number
  permCode: string
  permName: string
  path: string | null
  icon?: string | null
  children: MenuNode[]
}

export interface UserInfo {
  id: number
  username: string
  userType: number
  realName: string | null
  avatarUrl: string | null
  realnameStatus: number
  merchantId: number | null
  merchantName: string | null
  roles: string[]
  perms: string[]
}

export interface LoginResult {
  token: string
  tokenType: string
  expireSeconds: number
  /** 设计03 D1：7 天有效期的续期凭证，access token 过期后由 request.ts 静默换发 */
  refreshToken?: string
  refreshExpireSeconds?: number
  userInfo: UserInfo
  menus: MenuNode[]
}

export const loginApi = (data: LoginPayload) =>
  request.post<any, LoginResult>('/auth/login', data)

// POST /auth/refresh 只由 utils/request.ts 的 401 拦截器调用（裸 axios + refreshToken 头）。
// 这里不重复导出一个 helper：service 的请求拦截器会用访问凭证覆盖 Authorization，
// 拿它去续期等于把要续的那张票又塞回去。

export const getMe = () =>
  request.get<any, { userInfo: UserInfo }>('/auth/me')

export const getMenus = () =>
  request.get<any, MenuNode[]>('/auth/menus')

export const logoutApi = () => request.post('/auth/logout')

export const changePasswordApi = (oldPassword: string, newPassword: string) =>
  request.post('/auth/change-password', { oldPassword, newPassword })
