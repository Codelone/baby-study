export type Language = 'english' | 'chinese' | 'bilingual' | 'unknown'
export type SeriesSummary = {
  id: number
  name: string
  cover_url: string
  expected_count: number | null
  completeness: 'unknown' | 'partial' | 'complete'
}
export type Evidence = {
  field: string
  value: unknown
  source_url: string
  source_label: string
  status: 'confirmed' | 'candidate'
  collected_at: string
}
export type Book = {
  id: number
  title: string
  original_title: string
  aliases: string[]
  language: Language
  series_id: number | null
  series_ids: number[]
  series: SeriesSummary[]
  series_name: string
  series_description: string
  is_on_shelf: boolean
  isbn: string
  authors: string[]
  illustrators: string[]
  publisher: string
  edition: string
  cover_url: string
  cover_source_url: string
  description: string
  themes: string[]
}
export type BookSeries = SeriesSummary & {
  description: string
  family: string
  aliases: string[]
  isbn: string
  edition: string
  publisher: string
  cover_source_url: string
  books: Book[]
  total_count: number
  owned_count: number
}
export type BookDetail = Book & {
  translators: string[]
  publication_date: string
  binding: string
  pages: number | null
  reading_attributes: Record<
    string,
    { value: unknown; status: string; source_url: string }
  >
  evidence: Evidence[]
}
export type SeriesDetail = BookSeries & {
  publication_date: string
  contents: string
  evidence: Evidence[]
}
export const languageLabels: Record<Language, string> = {
  english: '英文',
  chinese: '中文',
  bilingual: '双语',
  unknown: '语言待确认',
}
export function coverage(
  group: Pick<BookSeries, 'total_count' | 'expected_count' | 'completeness'>,
) {
  return group.expected_count === null
    ? `已收录 ${group.total_count} 本 · 总册数待核实`
    : `已收录 ${group.total_count}/${group.expected_count} 本 · ${group.completeness === 'complete' ? '分册已完整核实' : '分册待补全'}`
}
