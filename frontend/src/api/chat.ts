/** 问答（SSE）与会话接口（docs/05 §5-§6）。 */

import { http } from '@/api/http'
import { postSse, type SseController, type SseHandlers } from '@/utils/sse'
import type { ChatMessage, Conversation, PageResult } from '@/types/api'

export interface ChatPayload {
  query: string
  conversation_id?: string | null
  top_k?: number
  score_threshold?: number
  answer_style?: string
}

export const chatApi = {
  /** 新会话问答：POST /kbs/{kb_id}/chat/stream */
  stream: (kbId: string, payload: ChatPayload, handlers: SseHandlers): SseController =>
    postSse(`/api/v1/kbs/${kbId}/chat/stream`, { ...payload }, handlers),
  /** 续聊：POST /conversations/{id}/continue */
  continueStream: (conversationId: string, payload: ChatPayload, handlers: SseHandlers): SseController =>
    postSse(`/api/v1/conversations/${conversationId}/continue`, { ...payload }, handlers),
  conversations: (params?: { kb_id?: string; keyword?: string; page?: number; page_size?: number }) =>
    http.get<PageResult<Conversation>>('/conversations', params),
  conversation: (conversationId: string) => http.get<Conversation>(`/conversations/${conversationId}`),
  messages: (conversationId: string, params?: { page?: number; page_size?: number }) =>
    http.get<PageResult<ChatMessage>>(`/conversations/${conversationId}/messages`, params),
  rename: (conversationId: string, title: string) =>
    http.patch<{ id: string; title: string }>(`/conversations/${conversationId}`, { title }),
  remove: (conversationId: string) => http.delete<{ id: string }>(`/conversations/${conversationId}`)
}
