/**
 * 统一 HTTP 客户端（docs/02 §6：所有请求都走这里）。
 * - 自动附带 Bearer Token
 * - 统一解包 `{code,message,data}`（成功码：主后端 0 / ask ai 后端 200），其余抛 ApiError
 * - 401 时清理登录态并跳转登录页
 */

import { clearAuth, getToken } from '@/stores/auth'

const PREFIX = '/api/v1'
/** ask ai 阶段五后端（FastAPI :8080）的路由挂在 /api 下（没有 v1），单独一套前缀 */
const ASK_AI_PREFIX = '/api'

export class ApiError extends Error {
  code: number
  detail?: unknown

  constructor(code: number, message: string, detail?: unknown) {
    super(message)
    this.code = code
    this.detail = detail
  }
}

type Query = Record<string, string | number | boolean | undefined | null>

export function buildQuery(params?: Query): string {
  if (!params) return ''
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.append(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

async function parse<T>(resp: Response): Promise<T> {
  const text = await resp.text()
  let payload: { code?: number; message?: string; data?: T; detail?: unknown } = {}
  try {
    payload = text ? JSON.parse(text) : {}
  } catch {
    throw new ApiError(resp.status, text.slice(0, 200) || '响应不是合法 JSON')
  }
  const code = payload.code ?? (resp.ok ? 0 : resp.status)
  // 成功码：主后端用 0；ask ai 阶段五后端用 200（HTTP 语义码）
  if (code !== 0 && code !== 200) {
    if (code === 40101) {
      clearAuth()
      if (!location.hash.includes('/login')) location.hash = '#/login'
    }
    throw new ApiError(code, payload.message || `请求失败（${code}）`, payload.detail)
  }
  return payload.data as T
}

export async function request<T>(
  method: string,
  path: string,
  options: { body?: unknown; params?: Query; formData?: FormData; prefix?: string } = {}
): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let body: BodyInit | undefined
  if (options.formData) {
    body = options.formData
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }

  const resp = await fetch(`${options.prefix ?? PREFIX}${path}${buildQuery(options.params)}`, {
    method,
    headers,
    body
  })
  return parse<T>(resp)
}

export const http = {
  get: <T>(path: string, params?: Query) => request<T>('GET', path, { params }),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, { body }),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, { body }),
  delete: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, formData: FormData) => request<T>('POST', path, { formData })
}

/**
 * ask ai 阶段五后端客户端（知识库文档管理）：路由为 /api/*，与主后端共用一个 fetch 封装。
 */
export const askAi = {
  get: <T>(path: string, params?: Query) =>
    request<T>('GET', path, { params, prefix: ASK_AI_PREFIX }),
  post: <T>(path: string, body?: unknown) =>
    request<T>('POST', path, { body, prefix: ASK_AI_PREFIX }),
  upload: <T>(path: string, formData: FormData, params?: Query) =>
    request<T>('POST', path, { formData, params, prefix: ASK_AI_PREFIX }),
  delete: <T>(path: string, params?: Query) =>
    request<T>('DELETE', path, { params, prefix: ASK_AI_PREFIX })
}

/** 健康检查（不带 /api/v1 前缀） */
export async function health(): Promise<Record<string, unknown>> {
  const resp = await fetch('/health')
  const payload = await resp.json()
  return payload.data ?? payload
}
