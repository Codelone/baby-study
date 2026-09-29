import { useEffect, useRef, useState } from 'react'
import { csrfToken, request, RequestError } from './api'
import { coverage, languageLabels } from './catalog'
import type { BookDetail, SeriesDetail } from './catalog'

export function Cover({ url, title }: { url: string; title: string }) {
  const [failedUrl, setFailedUrl] = useState('')
  return url && failedUrl !== url ? (
    <img
      className="book-cover"
      src={url}
      alt={title + '封面'}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailedUrl(url)}
    />
  ) : (
    <div
      className="book-cover cover-placeholder"
      aria-label={title + '：封面待补全'}
    >
      <span aria-hidden="true">BOOK</span>
      <span>{title}</span>
    </div>
  )
}

const fieldLabels: Record<string, string> = {
  title: '书名',
  name: '套装名',
  language: '语言',
  authors: '作者',
  illustrators: '绘者',
  translators: '译者',
  publisher: '出版社',
  publication_date: '出版时间',
  pages: '页数',
  binding: '装帧',
  edition: '版本',
  isbn: 'ISBN',
  cover_url: '封面',
  cover_source_url: '封面出处',
  description: '简介',
  themes: '主题',
  reading_attributes: '阅读属性',
  expected_count: '总册数',
  completeness: '完整性',
  contents: '内容与附件',
  family: '归属',
  aliases: '别名',
  original_title: '原文名',
}
const readingLabels: Record<string, string> = {
  point_reading: '点读适用版本',
  publisher_age: '出版方建议年龄',
  distributor_age: '发行商建议年龄',
  native_level: '原生分级',
  lexile: '蓝思声明',
}
function display(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join('、') : '待核实'
  if (value === null || value === undefined || value === '') return '待核实'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export default function CatalogDetails({
  target,
  onClose,
  onBook,
  canManage,
  onArchived,
  onAuthRequired,
  onManageSeries,
}: {
  target: { kind: 'book' | 'series'; id: number }
  onClose: () => void
  onBook: (id: number) => void
  canManage: boolean
  onArchived: () => Promise<void>
  onAuthRequired: () => void
  onManageSeries: (target: { kind: 'book' | 'series'; id: number }) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const opener = useRef(document.activeElement as HTMLElement | null)
  const [data, setData] = useState<BookDetail | SeriesDetail | null>(null)
  const [error, setError] = useState('')
  const [archiveConfirm, setArchiveConfirm] = useState(false)
  const [archiving, setArchiving] = useState(false)
  const archivePanel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (archiveConfirm) archivePanel.current?.focus()
  }, [archiveConfirm])
  useEffect(() => {
    dialog.current?.showModal()
    return () => {
      if (opener.current?.isConnected) opener.current.focus()
      else
        (
          document.getElementById('catalog-heading') ||
          document.getElementById('shelf-heading')
        )?.focus()
    }
  }, [])
  useEffect(() => {
    const abort = new AbortController()
    setData(null)
    setError('')
    setArchiveConfirm(false)
    request<BookDetail | SeriesDetail>(
      (target.kind === 'book' ? '/books/' : '/series/') + target.id,
      { signal: abort.signal },
    )
      .then(setData)
      .catch((cause: Error) => {
        if (!abort.signal.aborted) setError(cause.message)
      })
    return () => abort.abort()
  }, [target.kind, target.id])
  const book = data && 'title' in data ? data : null
  const series = data && 'name' in data ? data : null
  const title = book?.title || series?.name || '图书详情'

  async function archive() {
    if (!book) return
    setArchiving(true)
    setError('')
    try {
      await request<void>('/books/' + book.id, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': csrfToken() },
      })
      setData({ ...book, catalog_status: 'archived' })
      setArchiveConfirm(false)
      try {
        await onArchived()
        dialog.current?.close()
      } catch {
        setError('图书已归档，刷新书目暂时失败。请刷新页面查看，无需再次归档。')
      }
    } catch (cause) {
      const failure = cause as RequestError
      setError(failure.message || '归档失败，请稍后重试。')
      if (failure.code === 'not_authenticated') onAuthRequired()
    } finally {
      setArchiving(false)
    }
  }
  return (
    <dialog
      className="catalog-dialog"
      ref={dialog}
      aria-labelledby="detail-title"
      onClose={onClose}
      onCancel={(event) => {
        if (archiving) event.preventDefault()
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return
        const controls = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
          ),
        ).filter((element) => element.getClientRects().length > 0)
        const first = controls[0]
        const last = controls[controls.length - 1]
        if (
          (event.shiftKey && document.activeElement === first) ||
          (!event.shiftKey && document.activeElement === last)
        ) {
          event.preventDefault()
          const nextFocus = event.shiftKey ? last : first
          nextFocus?.focus()
        }
      }}
    >
      <div className="dialog-toolbar">
        <p className="eyebrow">CATALOG · 可追溯书目</p>
        <button
          className="text-button"
          type="button"
          onClick={() => dialog.current?.close()}
          autoFocus
          aria-label="关闭图书详情"
          disabled={archiving}
        >
          关闭 ×
        </button>
      </div>
      <h2 id="detail-title">{title}</h2>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {!data ? (
        <p role="status">正在读取详情…</p>
      ) : (
        <>
          <div className="detail-overview">
            <Cover url={data.cover_url} title={title} />
            <div>
              {book?.catalog_status === 'archived' && (
                <p className="status-tag archived-tag">
                  已归档 · 原书架与阅读记录保留
                </p>
              )}
              <p>
                {book
                  ? languageLabels[book.language]
                  : series && coverage(series)}
              </p>
              <p>{data.edition || '具体版本资料待核实'}</p>
              <p>{data.description || '简介待补全'}</p>
              {data.cover_source_url && (
                <a
                  href={data.cover_source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  封面出处 ↗
                </a>
              )}
            </div>
          </div>
          <dl className="metadata-grid">
            {(book
              ? [
                  ['ISBN', book.isbn],
                  ['作者', book.authors],
                  ['绘者', book.illustrators],
                  ['译者', book.translators],
                  ['出版社', book.publisher],
                  ['出版时间', book.publication_date],
                  ['装帧', book.binding],
                  ['页数', book.pages],
                  ['原文名', book.original_title],
                  ['主题', book.themes],
                ]
              : series
                ? [
                    ['套装 ISBN', series.isbn],
                    ['品牌/系列归属', series.family],
                    ['出版社', series.publisher],
                    ['出版时间', series.publication_date],
                  ]
                : []
            ).map(([label, value]) => (
              <div key={String(label)}>
                <dt>{String(label)}</dt>
                <dd>{display(value)}</dd>
              </div>
            ))}
          </dl>
          {book && (
            <>
              <h3>阅读属性</h3>
              {Object.keys(book.reading_attributes).length === 0 ? (
                <p>点读、建议年龄、原生级别及蓝思资料待核实。</p>
              ) : (
                <dl className="reading-claims">
                  {Object.entries(book.reading_attributes).map(
                    ([key, claim]) => (
                      <div key={key}>
                        <dt>{readingLabels[key] || key}</dt>
                        <dd>
                          {display(claim.value)} ·{' '}
                          {claim.status === 'confirmed'
                            ? '来源已核实'
                            : '待核对'}{' '}
                          <a
                            href={claim.source_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            出处 ↗
                          </a>
                        </dd>
                      </div>
                    ),
                  )}
                </dl>
              )}
              <p className="book-meta">
                未知蓝思保持为空；不同分级体系不作换算。
              </p>
              {book.series.length > 0 && (
                <p>所属套装：{book.series.map((s) => s.name).join('、')}</p>
              )}
            </>
          )}
          {series && (
            <>
              <h3>已收录分册</h3>
              <p>{coverage(series)}。只加入已收录的实际读物。</p>
              <ul className="detail-members">
                {series.books.map((member) => (
                  <li key={member.id}>
                    <button
                      className="text-button"
                      type="button"
                      onClick={() => onBook(member.id)}
                    >
                      {member.title} →
                    </button>
                    {member.series_ids.length > 1 && (
                      <small>同一本书，阅读状态共享</small>
                    )}
                  </li>
                ))}
              </ul>
              <h3>内容与附属材料</h3>
              <p>{series.contents || '附属材料说明待核实'}</p>
            </>
          )}
          {canManage && (
            <button
              type="button"
              className="secondary-button"
              disabled={archiving}
              onClick={() => onManageSeries(target)}
            >
              维护系列 →
            </button>
          )}
          <h3>资料来源</h3>
          {data.evidence.length ? (
            <ul className="evidence-list">
              {data.evidence.map((source, index) => (
                <li key={index}>
                  <span>
                    {fieldLabels[source.field] || source.field} ·{' '}
                    {(source.kind || source.origin_kind) === 'manual' ||
                    (source.kind || source.origin_kind) === 'photo'
                      ? '人工确认'
                      : source.status === 'confirmed'
                        ? '已核实'
                        : '候选'}
                  </span>
                  {/^https?:\/\//i.test(source.source_url) ? (
                    <a
                      href={source.source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {source.label || source.source_label || '查看来源'} ↗
                    </a>
                  ) : source.photo_url?.startsWith('/baby/study/') ? (
                    <a href={source.photo_url} target="_blank" rel="noreferrer">
                      {source.label || source.source_label || '照片来源'} ↗
                    </a>
                  ) : (
                    <span>
                      {source.label ||
                        source.source_label ||
                        ((source.kind || source.origin_kind) === 'photo'
                          ? '照片识别'
                          : '人工录入')}
                    </span>
                  )}
                  <small>
                    {source.collected_at
                      ? '记录于 ' +
                        new Date(source.collected_at).toLocaleDateString(
                          'zh-CN',
                        )
                      : '来源日期待核对'}
                  </small>
                </li>
              ))}
            </ul>
          ) : (
            <p>当前记录来自历史清单，联网资料待核实。</p>
          )}
          {book && canManage && book.catalog_status !== 'archived' && (
            <section className="archive-actions">
              {!archiveConfirm ? (
                <button
                  className="remove-button"
                  type="button"
                  onClick={() => setArchiveConfirm(true)}
                >
                  归档此图书
                </button>
              ) : (
                <div
                  className="confirmation-panel"
                  role="group"
                  aria-label="确认归档图书"
                  ref={archivePanel}
                  tabIndex={-1}
                >
                  <h3>确认归档“{book.title}”？</h3>
                  <p>
                    归档后将从可选系统书目中隐藏。原有书架、阅读状态、系列关系和历史记录会保留，并显示归档标签。
                  </p>
                  <div className="management-actions">
                    <button
                      className="danger-button"
                      type="button"
                      disabled={archiving}
                      onClick={() => void archive()}
                    >
                      {archiving ? '正在归档…' : '确认归档'}
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={archiving}
                      onClick={() => setArchiveConfirm(false)}
                    >
                      返回详情
                    </button>
                  </div>
                </div>
              )}
            </section>
          )}
        </>
      )}
    </dialog>
  )
}
