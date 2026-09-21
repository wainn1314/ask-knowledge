/** 出题 / 判分接口（docs/05 §7）。错题本见 `api/wrongBook.ts`。 */

import { http } from '@/api/http'
import type { AnswerValue, PageResult, Quiz, QuizBrief, QuizResult } from '@/types/api'

export interface QuizCreatePayload {
  kb_id: string
  types: string[]
  count: number
  difficulty: string
  topic?: string | null
}

export const quizApi = {
  create: (payload: QuizCreatePayload) => http.post<Quiz>('/quizzes', payload),
  list: (params?: { kb_id?: string; page?: number; page_size?: number }) =>
    http.get<PageResult<QuizBrief>>('/quizzes', params),
  detail: (quizId: string, includeAnswer = true) =>
    http.get<Quiz>(`/quizzes/${quizId}`, { include_answer: includeAnswer }),
  submit: (quizId: string, answers: { question_id: string; answer: AnswerValue }[]) =>
    http.post<QuizResult>(`/quizzes/${quizId}/answers`, answers),
  result: (quizId: string) => http.get<QuizResult>(`/quizzes/${quizId}/result`),
  remove: (quizId: string) => http.delete<{ quiz_id: string }>(`/quizzes/${quizId}`)
}
