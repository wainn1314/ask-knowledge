/** 知识库文档管理（ask ai 阶段五后端：POST/GET/DELETE /api/knowledge/*）。
 *
 * 注意：这是**直连 Dify 数据集**的排障通道（一次只认一个数据集）。
 * 主后端的知识库流水线（解析 / 审核 / 切片入库）请走 `@/api/document` 的
 * `/kbs/{kb_id}/documents`，两边对应关系是 `Kb.dify_dataset_id`。
 */

import { askAi } from '@/api/http'
import type { KnowledgeDocumentPage, KnowledgeUploadResult } from '@/types/api'

export const knowledgeApi = {
  /** 文档列表（分页）；传 datasetId 时只查该知识库自己的数据集 */
  list: (params?: { page?: number; limit?: number; datasetId?: string }) =>
    askAi.get<KnowledgeDocumentPage>('/knowledge/documents', {
      page: params?.page,
      limit: params?.limit,
      dataset_id: params?.datasetId
    }),
  /** 上传单个文档：multipart/form-data，字段名固定为 file */
  upload: (file: File, datasetId?: string) => {
    const form = new FormData()
    form.append('file', file, file.name)
    return askAi.upload<KnowledgeUploadResult>('/knowledge/upload', form, { dataset_id: datasetId })
  },
  /** 删除文档（Dify 侧同步删除其分片） */
  remove: (documentId: string, datasetId?: string) =>
    askAi.delete<null>(`/knowledge/documents/${documentId}`, { dataset_id: datasetId })
}
