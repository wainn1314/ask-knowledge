/** 展示用格式化工具。 */

export function formatTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (num: number) => String(num).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export const QUESTION_TYPES: Record<string, string> = {
  single: '单选',
  multi: '多选',
  judge: '判断',
  short: '简答'
}

export const WRONG_STATUS: Record<string, string> = {
  unmastered: '未掌握',
  reviewing: '复习中',
  mastered: '已掌握'
}

/** 文档解析通道（document.parse_mode）展示文案。 */
export const PARSE_MODES: Record<string, string> = {
  builtin: '内置解析',
  mineru_local: 'MinerU 本地',
  mineru_api: 'MinerU API',
  dify: 'Dify 侧解析'
}

/** 解析通道标签：`dify` = 本地不解析，原文件直传 Dify（自带提取器 / 知识库流水线） */
export function parseModeLabel(mode?: string | null): string {
  if (!mode) return '-'
  return PARSE_MODES[mode] ?? mode
}

/** 把答案统一渲染成可读文本 */
export function answerText(answer: unknown): string {
  if (answer === null || answer === undefined || answer === '') return '-'
  if (typeof answer === 'boolean') return answer ? '正确' : '错误'
  if (Array.isArray(answer)) return answer.map((item) => String(item)).join('、')
  return String(answer)
}
