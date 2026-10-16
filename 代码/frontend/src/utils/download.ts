/**
 * 二进制进出：CSV 导出（服务端生成）与受控文件查看（资质证照原件）。
 *
 * 为什么不用 service（utils/request 里的 axios 实例）：
 *  1. 响应拦截器只认 {code,message,data} 信封，Blob 进来会被当成"信封解析失败"弹错误提示；
 *  2. 二进制流不该被业务层再包一层 Promise 拆包。
 * 所以这里用裸 axios，和 request.ts 里静默续期那条通道同一个做法。
 *
 * 为什么 CSV 由服务端生成而不是前端拼：导出必须带数据范围和权限口径
 * （商家只能导本商家、上限 5000 行、手机号是解密后的完整号码），
 * 前端手上只有当前页 10 行，拼出来的"导出"其实只是"打印这一页"。
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { TOKEN_KEY } from '@/utils/request'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

export interface DownloadResult {
  count: number
  truncated: boolean
}

/** 服务端把 CSV 当附件返回；出错时同一个地址会返回 JSON 信封（Blob 形态），要还原成可读消息。 */
async function readErrorBlob(blob: Blob): Promise<string> {
  try {
    const body = JSON.parse(await blob.text())
    return body?.message || '导出失败'
  } catch {
    return '导出失败'
  }
}

export async function downloadCsv(
  url: string,
  params: Record<string, unknown>,
  filename: string,
): Promise<DownloadResult> {
  try {
    const resp = await axios.get(`${API_BASE}${url}`, {
      params,
      responseType: 'blob',
      timeout: 60000,
      headers: {
        Authorization: `Bearer ${localStorage.getItem(TOKEN_KEY) || ''}`,
        'Device-Type': 'PC',
      },
    })
    const blob = resp.data as Blob
    if (blob.type.includes('application/json')) {
      throw new Error(await readErrorBlob(blob))
    }
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([blob], { type: 'text/csv;charset=utf-8' }))
    a.download = filename
    a.click()
    URL.revokeObjectURL(a.href)
    return {
      count: Number(resp.headers['x-export-count'] ?? 0),
      truncated: resp.headers['x-export-truncated'] === '1',
    }
  } catch (err: any) {
    const data = err?.response?.data
    const message = data instanceof Blob ? await readErrorBlob(data) : (data?.message || err?.message)
    ElMessage.error(message || '导出失败')
    throw new Error(message || '导出失败')
  }
}

/**
 * 查看服务端受控文件（资质证照原件这类）。
 *
 * 为什么不能 `<img :src>` / `<a href>` 直接指到接口地址：那道门只认 Authorization 头，
 * 浏览器发起的资源请求不会带它；把 token 拼进 query 又等于把凭证写进地址栏和历史记录。
 * 所以还是裸 axios 拉一次 blob，再交给浏览器自己的查看器。
 *
 * window.open 在 await 之后已经不在用户手势里了，可能被拦截；拦下来就退回落盘打开，
 * 至少让人看得到东西，而不是点了没反应。
 *
 * 和 downloadCsv 不一样，这里失败只提示、不往外抛：调用点是列表里的「原件」链接，
 * 没有需要回滚的后续动作，让 Promise 带着一个已经弹过的错误继续飞只会变成控制台红字。
 */
export async function viewStoredFile(url: string, filename: string): Promise<void> {
  try {
    const resp = await axios.get(`${API_BASE}${url}`, {
      responseType: 'blob',
      timeout: 60000,
      headers: {
        Authorization: `Bearer ${localStorage.getItem(TOKEN_KEY) || ''}`,
        'Device-Type': 'PC',
      },
    })
    const raw = resp.data as Blob
    if (raw.type.includes('application/json')) {
      throw new Error(await readErrorBlob(raw))
    }
    // 保住服务端给的 MIME：PDF 命中它才会内联预览，丢了就变成一堆乱码文本
    const href = URL.createObjectURL(new Blob([raw], { type: raw.type || 'application/octet-stream' }))
    const win = window.open(href, '_blank')
    if (!win) {
      const a = document.createElement('a')
      a.href = href
      a.download = filename
      a.click()
      ElMessage.info('浏览器拦截了新窗口，原件已改为下载')
    }
    // 新标签页是异步读取这个 URL 的，立刻 revoke 会让那边变成空页
    setTimeout(() => URL.revokeObjectURL(href), 60000)
  } catch (err: any) {
    const data = err?.response?.data
    const message = data instanceof Blob ? await readErrorBlob(data) : (data?.message || err?.message)
    ElMessage.error(message || '原件读取失败')
  }
}
