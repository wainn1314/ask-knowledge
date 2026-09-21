/**
 * SSE 客户端：POST + ReadableStream 手工解析（EventSource 不支持 POST / 自定义头）。
 *
 * 后端事件契约（docs/05 §5）：
 *   meta      {conversation_id, message_id, kb_id}
 *   sources   {items: SourceItem[]}
 *   message   {delta}
 *   usage     {prompt_tokens, completion_tokens, total_tokens, latency_ms}
 *   moderation{scene, flagged, message}
 *   error     {code, message}
 *   done      {message_id, conversation_id, sources_count, finish_reason}
 */

import { getToken } from '@/stores/auth'

export interface SseHandlers {
  onEvent: (event: string, data: Record<string, unknown>) => void
  onError?: (error: Error) => void
  onClose?: () => void
}

export interface SseController {
  abort: () => void
}

export function postSse(
  path: string,
  body: Record<string, unknown>,
  handlers: SseHandlers
): SseController {
  const controller = new AbortController()

  const run = async () => {
    const token = getToken()
    let resp: Response
    try {
      resp = await fetch(path, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {})
        },
        body: JSON.stringify(body),
        signal: controller.signal
      })
    } catch (error) {
      if ((error as Error).name !== 'AbortError') handlers.onError?.(error as Error)
      return
    }

    if (!resp.ok || !resp.body) {
      // 非 2xx 时后端返回统一 JSON 错误体
      const text = await resp.text().catch(() => '')
      let message = `请求失败（HTTP ${resp.status}）`
      try {
        const payload = JSON.parse(text)
        message = payload.message || message
      } catch {
        /* 保持默认提示 */
      }
      handlers.onError?.(new Error(message))
      return
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let eventName = ''
    const dataLines: string[] = []

    const flush = () => {
      if (!eventName && dataLines.length === 0) return
      const raw = dataLines.join('\n')
      dataLines.length = 0
      const name = eventName || 'message'
      eventName = ''
      if (!raw) return
      try {
        handlers.onEvent(name, JSON.parse(raw) as Record<string, unknown>)
      } catch {
        handlers.onEvent(name, { raw })
      }
    }

    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let index = buffer.indexOf('\n')
        while (index >= 0) {
          const line = buffer.slice(0, index).replace(/\r$/, '')
          buffer = buffer.slice(index + 1)
          if (line === '') {
            flush()
          } else if (line.startsWith('event:')) {
            eventName = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim())
          }
          index = buffer.indexOf('\n')
        }
      }
      flush()
    } catch (error) {
      if ((error as Error).name !== 'AbortError') handlers.onError?.(error as Error)
    } finally {
      handlers.onClose?.()
    }
  }

  void run()
  return { abort: () => controller.abort() }
}
