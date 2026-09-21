/** 错题本接口（docs/05 §8）。 */

import { http } from '@/api/http'
import type { PageResult, WrongBookDetail, WrongBookItem } from '@/types/api'

export const wrongBookApi = {
  list: (params?: { kb_id?: string; status?: string; page?: number; page_size?: number }) =>
    http.get<PageResult<WrongBookItem>>('/wrong-book', params),
  detail: (wrongId: string) => http.get<WrongBookDetail>(`/wrong-book/${wrongId}`),
  update: (wrongId: string, payload: { status?: string; remark?: string }) =>
    http.patch<WrongBookItem>(`/wrong-book/${wrongId}`, payload),
  remove: (wrongId: string) => http.delete<{ id: string }>(`/wrong-book/${wrongId}`),
  /** 重做：返回题目（不含答案），作答仍走 /quizzes/{quiz_id}/answers */
  retry: (wrongId: string) =>
    http.post<{
      id: string
      question_id: string
      type: string
      stem: string
      options?: { key: string; text: string }[] | null
      status: string
      wrong_count: number
    }>(`/wrong-book/${wrongId}/retry`)
}
