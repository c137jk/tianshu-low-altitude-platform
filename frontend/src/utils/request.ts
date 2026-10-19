import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { ElMessage } from 'element-plus'

/**
 * 统一 HTTP 客户端（设计03 全局约定）
 * - 自动带 JWT（Authorization: Bearer）与 Device-Type 头
 * - 只认统一信封 {code,message,data}：code=0 直接把 data 抛给业务层
 * - 401/40100：先拿 refreshToken 静默续一次并重放原请求，续不动才清登录态回登录页
 */
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 10000,
})

/**
 * 登录态在 localStorage 里就这两个键，谁写谁读都走下面这组函数，
 * 免得哪天改了键名，续期逻辑去捞一个不存在的 token。
 */
export const TOKEN_KEY = 'ts_token'
export const REFRESH_KEY = 'ts_refresh_token'

export function setTokens(token: string, refreshToken?: string) {
  localStorage.setItem(TOKEN_KEY, token)
  if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken)
}

export function clearTokens() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

service.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  config.headers['Device-Type'] = 'PC'
  return config
})

function forceLogout(message: string) {
  clearTokens()
  ElMessage.error(message)
  // 用整页跳转避免 request ↔ router 循环依赖
  if (!location.pathname.endsWith('/login')) {
    location.href = '/login'
  }
}

/**
 * 访问凭证有效期：PC 8 小时 / 移动端 2 小时（设计03 D-03）。
 * 没有续期的话，调度员早上登录、下午第一次点击就是一个 401，
 * 正在填的表单直接把人踢回登录页。所以先静默换一张，再决定要不要踢。
 *
 * 三条硬规矩：
 *  1. 登录/续期/注册自己不能触发续期，否则 401 会递归成死循环；
 *  2. 同一时刻只允许一条续期请求在飞（refreshing 门闩 + 等待队列），
 *     大屏 2.5s 一拉、页面里并发好几个请求，不设门闩就是并发打后端；
 *  3. 续期失败必须清干净，别拿一张已经失效的 refresh token 反复试。
 */
const AUTH_URLS = ['/auth/login', '/auth/refresh', '/auth/register']
let refreshing: Promise<string> | null = null

function requestNewToken(): Promise<string> {
  if (refreshing) return refreshing
  const refreshToken = localStorage.getItem(REFRESH_KEY)
  if (!refreshToken) return Promise.reject(new Error('no-refresh-token'))
  // 续期用裸 axios：走 service 会被拦截器再套一层 Authorization / 递归续期
  refreshing = axios
    .post(
      `${import.meta.env.VITE_API_BASE || '/api'}/auth/refresh`,
      {},
      { headers: { Authorization: `Bearer ${refreshToken}`, 'Device-Type': 'PC' }, timeout: 10000 },
    )
    .then((resp) => {
      const body = resp.data
      if (body?.code !== 0 || !body.data?.token) throw new Error(body?.message || '续期失败')
      localStorage.setItem(TOKEN_KEY, body.data.token)
      if (body.data.refreshToken) localStorage.setItem(REFRESH_KEY, body.data.refreshToken)
      return body.data.token as string
    })
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

async function retryWithFreshToken(config: any, message: string) {
  try {
    config.headers.Authorization = `Bearer ${await requestNewToken()}`
    return await service(config)
  } catch {
    forceLogout(message)
    return Promise.reject(new Error(message))
  }
}

function isAuthUrl(url?: string) {
  return !!url && AUTH_URLS.some((u) => url!.includes(u))
}

service.interceptors.response.use(
  (resp: AxiosResponse) => {
    const body = resp.data
    // 文件流等非信封响应直接放行
    if (typeof body !== 'object' || body === undefined) return body
    if (body.code === 0) return body.data
    if (body.code === 40100) {
      const cfg = resp.config
      if (!isAuthUrl(cfg?.url) && localStorage.getItem(REFRESH_KEY)) {
        return retryWithFreshToken(cfg, body.message || '登录已失效，请重新登录')
      }
      forceLogout(body.message || '登录已失效，请重新登录')
    } else {
      ElMessage.error(body.message || '请求失败')
    }
    return Promise.reject(new Error(body.message || 'biz error'))
  },
  (err) => {
    const status = err.response?.status
    const message = err.response?.data?.message
    const cfg = err.config
    if (status === 401) {
      if (!isAuthUrl(cfg?.url) && localStorage.getItem(REFRESH_KEY)) {
        return retryWithFreshToken(cfg, message || '登录已失效，请重新登录')
      }
      forceLogout(message || '登录已失效，请重新登录')
    } else {
      ElMessage.error(message || err.message || '网络异常，请稍后再试')
    }
    return Promise.reject(err)
  },
)

/**
 * 运行时事实：响应拦截器已经把 {code,message,data} 拆成 data，
 * 所以 `request(url, cfg)` 直接拿到业务数据而不是 AxiosResponse。
 * 这里用交叉类型把这个事实告诉 TS（同时保留 AxiosInstance 上的 get/post 等方法签名，
 * 让 api/*.ts 里 `request.get<any, T>()` 的写法继续可用）。
 */
export interface ApiFetch {
  <T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
}

const request = service as unknown as ApiFetch & AxiosInstance

export default request
