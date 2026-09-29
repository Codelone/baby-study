import type { Language } from './catalog'

export type CatalogCapabilities = {
  can_manage_catalog: boolean
  realtime_configured: boolean
  batch_configured: boolean
}

export type BookFields = {
  title: string
  original_title: string
  language: Language
  isbn: string
  authors: string[]
  illustrators: string[]
  translators: string[]
  publisher: string
  publication_date: string
  binding: string
  pages: number | null
  edition: string
  description: string
  themes: string[]
  cover_url: string
  cover_source_url: string
}

export const fieldLabels: Record<keyof BookFields, string> = {
  title: '书名',
  original_title: '原文名',
  language: '语言',
  isbn: 'ISBN',
  authors: '作者',
  illustrators: '绘者',
  translators: '译者',
  publisher: '出版社',
  publication_date: '出版时间',
  binding: '装帧',
  pages: '页数',
  edition: '版本',
  description: '简介',
  themes: '主题',
  cover_url: '封面地址',
  cover_source_url: '封面出处',
}

export function emptyFields(): BookFields {
  return {
    title: '',
    original_title: '',
    language: 'unknown',
    isbn: '',
    authors: [],
    illustrators: [],
    translators: [],
    publisher: '',
    publication_date: '',
    binding: '',
    pages: null,
    edition: '',
    description: '',
    themes: [],
    cover_url: '',
    cover_source_url: '',
  }
}

export function cleanFields(fields: Partial<BookFields>): BookFields {
  const result = { ...emptyFields(), ...fields }
  for (const key of Object.keys(result) as (keyof BookFields)[]) {
    const value = result[key]
    if (Array.isArray(value)) {
      Object.assign(result, {
        [key]: value.map((s) => s.trim()).filter(Boolean),
      })
    } else if (typeof value === 'string') {
      Object.assign(result, { [key]: value.trim() })
    }
  }
  return result
}

export type IntakeIssue =
  string | { code?: string; message?: string; field?: string }
export type IntakeEvidence = {
  field: string
  value: unknown
  kind: 'photo' | 'web' | 'manual'
  photo_id?: string
  source_url?: string
  source_label?: string
  model_name?: string
}
export type IntakeGroup = {
  id: string
  photo_ids: string[]
  fields: Partial<BookFields>
  evidence: IntakeEvidence[]
  issues: IntakeIssue[]
  existing_book_id: number | null
  selected: boolean
  existing_book_candidates?: {
    id: number
    title: string
    isbn: string
    edition: string
    publisher: string
  }[]
  enrichment_candidates?: Candidate[]
}
export type IntakePhoto = {
  id: string
  original_name: string
  status: 'pending' | 'processing' | 'succeeded' | 'failed'
  error: { code?: string; message?: string } | null
  url: string
  extraction: {
    photo_type?: string
    fields?: Partial<BookFields>
    issues?: IntakeIssue[]
    model_name?: string
    usage?: {
      prompt_tokens?: number | null
      completion_tokens?: number | null
      total_tokens?: number | null
    } | null
  }
}
export type IntakeStatus =
  | 'queued'
  | 'processing'
  | 'awaiting_confirmation'
  | 'partial_failure'
  | 'failed'
  | 'cancelled'
  | 'confirmed'
export type ConfirmedBook = {
  group_id: string
  book_id: number
  created: boolean
}
export type Intake = {
  id: string
  mode: 'batch' | 'realtime'
  status: IntakeStatus
  revision: number
  generation: number
  groups: IntakeGroup[]
  unassigned_photo_ids: string[]
  photos: IntakePhoto[]
  errors: { code?: string; message?: string }[]
  progress: Record<string, number | string>
  confirmed_books: ConfirmedBook[]
  created_at: string
  updated_at: string
}
export type Candidate = {
  id: string
  fields: Partial<BookFields>
  evidence: IntakeEvidence[]
  issues: IntakeIssue[]
}
export type CandidateResult = { candidates: Candidate[]; issues: IntakeIssue[] }
export const intakeLabels: Record<IntakeStatus, string> = {
  queued: '等待识别',
  processing: '正在识别',
  awaiting_confirmation: '待核对入库',
  partial_failure: '部分照片识别失败',
  failed: '识别失败',
  cancelled: '已取消',
  confirmed: '已确认入库',
}
export function isRunning(intake: Intake) {
  return intake.status === 'queued' || intake.status === 'processing'
}
export function canEdit(intake: Intake) {
  return (
    intake.status === 'awaiting_confirmation' ||
    intake.status === 'partial_failure'
  )
}
export function issueMessage(issue: IntakeIssue) {
  return typeof issue === 'string'
    ? issue
    : issue.message || '资料存在疑点，请核对照片和版本。'
}
export function fieldValue(value: unknown): string {
  if (Array.isArray(value)) return value.filter(Boolean).join('、') || '未知'
  if (value === '' || value === null || value === undefined) return '未知'
  return typeof value === 'object' ? '待核对' : String(value)
}

export function intakeIdFromHash(): string {
  const match = /^#catalog\/intakes\/([0-9a-f-]{36})$/i.exec(
    window.location.hash,
  )
  return match?.[1] || ''
}
