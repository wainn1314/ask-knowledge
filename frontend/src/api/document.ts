/** 文档上传 / 状态 / 预览 / 分块（docs/05 §4）。 */

import { http } from '@/api/http'
import type {
  DocumentItem,
  DocumentStatus,
  PageResult,
  SourceItem,
  UploadResult
} from '@/types/api'

export const documentApi = {
  upload: (kbId: string, files: File[]) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file, file.name))
    return http.upload<UploadResult>(`/kbs/${kbId}/documents`, form)
  },
  list: (
    kbId: string,
    params?: { status?: string; keyword?: string; page?: number; page_size?: number }
  ) => http.get<PageResult<DocumentItem>>(`/kbs/${kbId}/documents`, params),
  detail: (documentId: string) => http.get<DocumentItem>(`/documents/${documentId}`),
  status: (documentId: string) => http.get<DocumentStatus>(`/documents/${documentId}/status`),
  preview: (documentId: string, page = 1, pageSize = 4000) =>
    http.get<{
      document_id: string
      filename: string
      mode?: string | null
      page: number
      page_size: number
      total_chars: number
      truncated: boolean
      content: string
    }>(`/documents/${documentId}/preview`, { page, page_size: pageSize }),
  chunks: (documentId: string, page = 1, pageSize = 20) =>
    http.get<{
      document_id: string
      total: number
      items: { segment_id: string; position: number; chars: number; content: string }[]
    }>(`/documents/${documentId}/chunks`, { page, page_size: pageSize }),
  retry: (documentId: string, stage?: 'parse' | 'audit' | 'index') =>
    http.post<{ document_id: string; task_id?: string | null; status: string }>(
      `/documents/${documentId}/retry`,
      stage ? { stage } : {}
    ),
  remove: (documentId: string) => http.delete<{ id: string }>(`/documents/${documentId}`)
}

/** 引用来源在文档预览里定位用（保持与后端 sources 结构一致） */
export type DocumentSource = SourceItem
