/** 知识库与成员接口（docs/05 §3）。 */

import { http } from '@/api/http'
import type { Kb, KbMember, PageResult, RetrievalRecord } from '@/types/api'

export interface KbPayload {
  name?: string
  description?: string | null
  visibility?: string
  chunk_size?: number
  chunk_overlap?: number
  top_k?: number
  score_threshold?: number
  retrieval_mode?: string
  rerank_enabled?: boolean
  semantic_weight?: number
}

export const kbApi = {
  list: (params?: { keyword?: string; page?: number; page_size?: number }) =>
    http.get<PageResult<Kb>>('/kbs', params),
  /** 单知识库模式：返回开关 + 那个「唯一知识库」（未开启时 kb=null，走多库交互） */
  defaultKb: () => http.get<{ single_kb_mode: boolean; kb: Kb | null }>('/kbs/default'),
  create: (payload: KbPayload) => http.post<Kb>('/kbs', payload),
  detail: (kbId: string) => http.get<Kb>(`/kbs/${kbId}`),
  update: (kbId: string, payload: KbPayload) => http.patch<Kb>(`/kbs/${kbId}`, payload),
  remove: (kbId: string) => http.delete<{ id: string }>(`/kbs/${kbId}`),
  members: (kbId: string) => http.get<{ items: KbMember[] }>(`/kbs/${kbId}/members`),
  addMember: (kbId: string, user_id: string, role: string) =>
    http.post<KbMember>(`/kbs/${kbId}/members`, { user_id, role }),
  updateMember: (kbId: string, userId: string, role: string) =>
    http.patch<KbMember>(`/kbs/${kbId}/members/${userId}`, { role }),
  removeMember: (kbId: string, userId: string) =>
    http.delete<{ user_id: string }>(`/kbs/${kbId}/members/${userId}`),
  retrievalTest: (kbId: string, query: string, top_k?: number, score_threshold?: number) =>
    http.post<{ query: string; total: number; records: RetrievalRecord[] }>(
      `/kbs/${kbId}/retrieval-test`,
      { query, top_k, score_threshold }
    )
}
