/** 与后端 `{code,message,data,request_id}` 对应的类型定义（docs/05）。 */

export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
  request_id?: string
}

export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface Tenant {
  id: string
  name: string
  code: string
}

export interface User {
  id: string
  email: string
  nickname?: string | null
  role: string
  tenant_id: string
  tenant?: Tenant | null
}

export interface TokenResult {
  access_token: string
  refresh_token?: string | null
  token_type: string
  expires_in: number
  user: User
}

export interface Kb {
  id: string
  name: string
  description?: string | null
  owner_id: string
  visibility: string
  /** 该知识库在 Dify 侧对应的数据集 ID（每个库一个，独立互不干扰） */
  dify_dataset_id?: string | null
  chunk_size: number
  chunk_overlap: number
  top_k: number
  score_threshold: number
  retrieval_mode: string
  rerank_enabled: boolean
  doc_count: number
  status: number
  role?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface KbMember {
  id: string
  kb_id: string
  user_id: string
  role: string
  created_at?: string | null
  user?: { id: string; email: string; nickname?: string | null } | null
}

export interface RetrievalRecord {
  content: string
  score: number
  document_id?: string | null
  document_name?: string | null
  segment_id?: string | null
  position?: number | null
}

export interface DocumentItem {
  id: string
  kb_id: string
  filename: string
  file_ext: string
  file_size: number
  status: string
  stage_progress: number
  audit_status: string
  parse_mode?: string | null
  page_count?: number | null
  chunk_count?: number | null
  error_code?: string | null
  error_message?: string | null
  retry_count: number
  created_at?: string | null
}

export interface DocumentStatus {
  document_id: string
  status: string
  stage_progress: number
  audit_status: string
  dify_indexing_status?: string | null
  error_code?: string | null
  error_message?: string | null
  retry_count: number
}

export interface UploadResult {
  accepted: { document_id: string; task_id?: string | null; filename: string; status: string }[]
  rejected: { filename: string; code: number; message: string }[]
}

export interface SourceItem {
  index: number
  document_id?: string | null
  document_name?: string | null
  segment_id?: string | null
  score: number
  content: string
  position?: number | null
}

export interface Conversation {
  id: string
  kb_id: string
  title?: string | null
  message_count: number
  last_message_at?: string | null
  dify_conversation_id?: string | null
  created_at?: string | null
  kb_name?: string | null
  dify_available?: boolean
}

export interface ChatMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | string
  content: string
  sources?: SourceItem[] | null
  audit_status: string
  latency_ms?: number | null
  created_at?: string | null
}

export interface QuestionOption {
  key: string
  text: string
}

export interface Question {
  id: string
  seq: number
  type: 'single' | 'multi' | 'judge' | 'short' | string
  difficulty: string
  stem: string
  options?: QuestionOption[] | null
  answer?: unknown
  analysis?: string | null
  grading_points?: string[] | null
  sources?: SourceItem[] | null
}

export interface Quiz {
  quiz_id: string
  status: string
  question_count: number
  title?: string | null
  questions: Question[]
}

export interface QuizBrief {
  id: string
  kb_id: string
  title?: string | null
  question_count: number
  status: string
  created_at?: string | null
  kb_name?: string | null
}

export type AnswerValue = string | string[] | boolean | null

export interface AnswerResultItem {
  question_id: string
  type?: string | null
  is_correct?: boolean | null
  score?: number | null
  correct_answer?: unknown
  user_answer?: AnswerValue
  feedback?: string | null
  analysis?: string | null
}

export interface QuizResult {
  quiz_id: string
  total: number
  correct: number
  score: number
  items: AnswerResultItem[]
  wrong_question_ids: string[]
}

export interface WrongBookItem {
  id: string
  question_id: string
  kb_id: string
  kb_name?: string | null
  type: string
  stem: string
  wrong_count: number
  status: string
  last_wrong_at?: string | null
  remark?: string | null
}

export interface WrongBookDetail extends WrongBookItem {
  options?: QuestionOption[] | null
  answer?: unknown
  analysis?: string | null
  grading_points?: string[] | null
  sources?: SourceItem[] | null
  last_feedback?: string | null
  last_user_answer?: AnswerValue
}

export interface HealthInfo {
  status: string
  app_env: string
  dify_mode: string
  parser_mode: string
  audit_provider: string
  worker_inline: boolean
  dify?: Record<string, unknown> | null
}

/* ── ask ai 阶段五后端（FastAPI :8080）知识库文档管理 /api/knowledge/* ── */

export interface KnowledgeDocument {
  id: string
  name: string
  /** Dify display_status：available / indexing / queuing / paused / error ... */
  status: string
  word_count: number
  created_at?: string | null
  tokens?: number | null
  error?: string | null
}

export interface KnowledgeDocumentPage {
  items: KnowledgeDocument[]
  total: number
  page: number
  limit: number
  has_more: boolean
}

export interface KnowledgeUploadResult {
  document: KnowledgeDocument
  batch?: string | null
}

