/** 登录态（轻量 reactive store：不引入 Pinia，够 MVP 用）。 */

import { computed, reactive } from 'vue'
import type { TokenResult, User } from '@/types/api'

const STORAGE_KEY = 'ask-kb-auth'

interface AuthState {
  token: string
  user: User | null
}

function load(): AuthState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { token: '', user: null }
    const parsed = JSON.parse(raw) as AuthState
    return { token: parsed.token || '', user: parsed.user || null }
  } catch {
    return { token: '', user: null }
  }
}

export const authState = reactive<AuthState>(load())

export const isLoggedIn = computed(() => Boolean(authState.token))

export function getToken(): string {
  return authState.token
}

export function setAuth(result: TokenResult): void {
  authState.token = result.access_token
  authState.user = result.user
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: result.access_token, user: result.user }))
}

export function patchUser(user: User): void {
  authState.user = user
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: authState.token, user }))
}

export function clearAuth(): void {
  authState.token = ''
  authState.user = null
  localStorage.removeItem(STORAGE_KEY)
}

/** 便捷：当前用户显示名 */
export function displayName(): string {
  return authState.user?.nickname || authState.user?.email || '未登录'
}
