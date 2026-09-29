import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { csrfToken, jsonRequest, request, RequestError } from './api'
import CatalogManagement from './CatalogManagement'
import SeriesSelector from './SeriesSelector'
import { coverage } from './catalog'
import type { Book, BookDetail, BookSeries, SeriesDetail } from './catalog'
import type { CatalogCapabilities } from './intake'

export type SeriesTarget =
  { kind: 'book' | 'series'; id: number } | { kind: 'create' }
type Fields = Pick<
  SeriesDetail,
  | 'name'
  | 'description'
  | 'family'
  | 'aliases'
  | 'isbn'
  | 'edition'
  | 'publisher'
  | 'publication_date'
  | 'cover_url'
  | 'cover_source_url'
  | 'contents'
  | 'expected_count'
  | 'completeness'
>
type Member = { book: Book; position: number }
type Pane = 'book' | 'details' | 'members' | 'intake'
const blank: Fields = {
  name: '',
  description: '',
  family: '',
  aliases: [],
  isbn: '',
  edition: '',
  publisher: '',
  publication_date: '',
  cover_url: '',
  cover_source_url: '',
  contents: '',
  expected_count: null,
  completeness: 'unknown',
}
const extraFields = [
  ['family', '品牌/系列归属'],
  ['isbn', '套装 ISBN'],
  ['edition', '版本说明'],
  ['publisher', '出版社'],
  ['publication_date', '出版时间'],
  ['cover_url', '封面地址'],
  ['cover_source_url', '封面出处'],
  ['contents', '内容与附属材料'],
] as const

export default function SeriesWorkspace({
  initialTarget,
  capabilities,
  onLibraryChanged,
  onAuthRequired,
  onUnsavedChange,
  onClose,
}: {
  initialTarget: SeriesTarget
  capabilities: CatalogCapabilities
  onLibraryChanged: () => Promise<void>
  onAuthRequired: () => void
  onUnsavedChange: (dirty: boolean) => void
  onClose: () => void
}) {
  const [id, setId] = useState(
    initialTarget.kind === 'series' ? initialTarget.id : null,
  )
  const [book, setBook] = useState<BookDetail | null>(null)
  const [series, setSeries] = useState<SeriesDetail | null>(null)
  const [options, setOptions] = useState<BookSeries[]>([])
  const [fields, setFields] = useState<Fields>(blank)
  const [members, setMembers] = useState<Member[]>([])
  const [pane, setPane] = useState<Pane>(
    initialTarget.kind === 'book' ? 'book' : 'details',
  )
  const [dirty, setDirty] = useState(false)
  const [orderDirty, setOrderDirty] = useState(false)
  const [intakeDirty, setIntakeDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null)
  const leavePanel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (pendingLeave) leavePanel.current?.focus()
  }, [pendingLeave])
  const [loaded, setLoaded] = useState(initialTarget.kind === 'create')
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState<Book[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [version, setVersion] = useState(0)
  const heading = useRef<HTMLHeadingElement>(null)
  const unsaved = dirty || orderDirty || intakeDirty
  useEffect(() => {
    heading.current?.focus()
  }, [])
  useEffect(() => {
    onUnsavedChange(unsaved)
    return () => onUnsavedChange(false)
  }, [unsaved, onUnsavedChange])
  useEffect(() => {
    if (!unsaved) return
    const preventLeave = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', preventLeave)
    return () => window.removeEventListener('beforeunload', preventLeave)
  }, [unsaved])
  const fail = useCallback(
    (cause: unknown) => {
      const failure = cause as RequestError
      setError(failure.message || '操作失败，请重试。')
      if (failure.code === 'not_authenticated') onAuthRequired()
    },
    [onAuthRequired],
  )
  useEffect(() => {
    const abort = new AbortController()
    setLoaded(false)
    const load = async () => {
      const result = await request<{ series: BookSeries[] }>('/series', {
        signal: abort.signal,
      })
      let detail: SeriesDetail | null = null
      let list: Member[] = []
      if (id !== null) {
        const [s, m] = await Promise.all([
          request<SeriesDetail>('/series/' + id, { signal: abort.signal }),
          request<{ members: Member[] }>('/series/' + id + '/members', {
            signal: abort.signal,
          }),
        ])
        detail = s
        list = m.members
      }
      const currentBook =
        initialTarget.kind === 'book'
          ? await request<BookDetail>('/books/' + initialTarget.id, {
              signal: abort.signal,
            })
          : null
      if (abort.signal.aborted) return
      setOptions(result.series)
      setSeries(detail)
      setFields(
        detail
          ? (Object.fromEntries(
              Object.keys(blank).map((key) => [
                key,
                detail[key as keyof Fields],
              ]),
            ) as Fields)
          : blank,
      )
      setMembers(list)
      setBook(currentBook)
      setDirty(false)
      setOrderDirty(false)
      setLoaded(true)
    }
    load().catch((cause) => {
      if (!abort.signal.aborted) fail(cause)
    })
    return () => abort.abort()
  }, [id, initialTarget, version, fail])
  useEffect(() => {
    if (pane !== 'members') return
    const abort = new AbortController()
    const timer = window.setTimeout(() => {
      request<{ books: Book[] }>('/books?q=' + encodeURIComponent(query), {
        signal: abort.signal,
      })
        .then((result) => {
          if (!abort.signal.aborted) setMatches(result.books)
        })
        .catch((cause) => {
          if (!abort.signal.aborted) fail(cause)
        })
    }, 250)
    return () => {
      abort.abort()
      window.clearTimeout(timer)
    }
  }, [query, pane, fail])
  function guardLeave(work: () => void) {
    if (unsaved) setPendingLeave(() => work)
    else work()
  }
  function switchPane(next: Pane) {
    if (busy || next === pane) return
    guardLeave(() => {
      if (dirty || orderDirty) setVersion((v) => v + 1)
      setDirty(false)
      setOrderDirty(false)
      setIntakeDirty(false)
      setPane(next)
      setError('')
      setNotice('')
    })
  }
  function choose(next: number | null) {
    if (busy) return
    guardLeave(() => {
      setId(next)
      setPane('details')
      setDirty(false)
      setOrderDirty(false)
      setIntakeDirty(false)
      setError('')
      setNotice('')
      setSelected([])
      setVersion((v) => v + 1)
    })
  }
  async function run(work: () => Promise<void>, message: string) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await work()
      setNotice(message)
      setVersion((v) => v + 1)
      try {
        await onLibraryChanged()
      } catch {
        setNotice(message + ' 书目刷新失败，请刷新页面；无需重复保存。')
      }
    } catch (cause) {
      fail(cause)
    } finally {
      setBusy(false)
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    await run(async () => {
      const result = await jsonRequest<SeriesDetail>(
        id === null ? '/series' : '/series/' + id,
        id === null ? 'POST' : 'PATCH',
        {
          ...fields,
          ...(id === null && book?.catalog_status === 'active'
            ? { book_ids: [book.id] }
            : {}),
          aliases: fields.aliases.map((alias) => alias.trim()).filter(Boolean),
        },
      )
      setId(result.id)
      setDirty(false)
    }, '系列资料已保存。')
  }
  function move(index: number, offset: number) {
    const next = [...members]
    ;[next[index], next[index + offset]] = [next[index + offset], next[index]]
    setMembers(next)
    setOrderDirty(true)
  }
  async function changeBookSeries(ids: number[]) {
    if (!book) return
    await run(async () => {
      for (const seriesId of ids.filter((pk) => !book.series_ids.includes(pk)))
        await jsonRequest('/series/' + seriesId + '/members', 'POST', {
          book_ids: [book.id],
        })
      for (const seriesId of book.series_ids.filter((pk) => !ids.includes(pk)))
        await request('/series/' + seriesId + '/members/' + book.id, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': csrfToken() },
        })
    }, '当前图书的系列关系已保存。')
  }
  return (
    <section
      className="catalog-management series-workspace"
      aria-labelledby="series-management-heading"
      aria-busy={busy}
    >
      <div className="intake-group-heading">
        <h3 id="series-management-heading" ref={heading} tabIndex={-1}>
          维护系列{book ? ' · ' + book.title : ''}
        </h3>
        <button
          type="button"
          className="text-button"
          disabled={busy}
          onClick={() => {
            guardLeave(onClose)
          }}
        >
          返回书目 ×
        </button>
      </div>
      <div className="management-actions">
        <label>
          <span>选择要维护的系列</span>
          <select
            disabled={busy || !loaded}
            value={id ?? ''}
            onChange={(e) =>
              choose(e.target.value ? Number(e.target.value) : null)
            }
          >
            <option value="">新建系列</option>
            {options.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="secondary-button"
          disabled={busy || !loaded}
          onClick={() => choose(null)}
        >
          新建系列
        </button>
      </div>
      <div className="management-tabs" role="group" aria-label="系列维护内容">
        {(
          [
            ...(book ? [['book', '关联当前书']] : []),
            ['details', '系列资料'],
            ['members', '成员维护'],
            ['intake', '添加图书'],
          ] as [Pane, string][]
        ).map(([key, label]) => (
          <button
            type="button"
            className={pane === key ? 'tab active' : 'tab'}
            aria-pressed={pane === key}
            key={key}
            disabled={
              busy ||
              !loaded ||
              ((key === 'members' || key === 'intake') && id === null)
            }
            onClick={() => switchPane(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}{' '}
          <button
            type="button"
            className="text-button"
            disabled={busy}
            onClick={() => {
              guardLeave(() => setVersion((v) => v + 1))
            }}
          >
            重新读取
          </button>
        </p>
      )}
      {notice && (
        <p className="success-banner" role="status">
          {notice}
        </p>
      )}
      {pendingLeave && (
        <div
          className="confirmation-panel"
          role="group"
          aria-label="放弃未保存修改"
          ref={leavePanel}
          tabIndex={-1}
        >
          <h4>有未保存的系列资料、顺序或录入草稿</h4>
          <p>继续会放弃这些修改；取消可以返回当前内容。</p>
          <div className="management-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={() => setPendingLeave(null)}
            >
              取消，继续编辑
            </button>
            <button
              type="button"
              className="remove-button"
              onClick={() => {
                const work = pendingLeave
                setPendingLeave(null)
                work()
              }}
            >
              放弃修改并继续
            </button>
          </div>
        </div>
      )}
      {!loaded ? (
        <p role="status">正在读取系列资料…</p>
      ) : (
        <>
          {pane === 'book' && book && (
            <>
              <p>
                为当前单本选择系列，勾选后立即保存。移出系列会保留单本及书架记录。
              </p>
              <SeriesSelector
                value={book.series_ids}
                options={options}
                disabled={busy || book.catalog_status !== 'active'}
                onChange={(ids) => void changeBookSeries(ids)}
                onCreated={(s) => setOptions((old) => [...old, s])}
                onAuthRequired={onAuthRequired}
              />
              <ul>
                {book.series.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      className="text-button"
                      disabled={busy}
                      onClick={() => choose(s.id)}
                    >
                      维护 {s.name} →
                    </button>
                    <button
                      type="button"
                      className="remove-button"
                      disabled={busy}
                      onClick={() =>
                        void changeBookSeries(
                          book.series_ids.filter((pk) => pk !== s.id),
                        )
                      }
                    >
                      移出此系列
                    </button>
                  </li>
                ))}
              </ul>
              {book.catalog_status !== 'active' && (
                <p>已归档图书不能新增系列关联，已有关系可以移出。</p>
              )}
            </>
          )}
          {pane === 'details' && (
            <form onSubmit={(e) => void save(e)}>
              <label>
                <span>系列名称 *</span>
                <input
                  required
                  maxLength={255}
                  value={fields.name}
                  disabled={busy}
                  onChange={(e) => {
                    setFields({ ...fields, name: e.target.value })
                    setDirty(true)
                  }}
                  placeholder="按具体套装、级别或版本命名"
                />
              </label>
              <label>
                <span>系列说明</span>
                <textarea
                  value={fields.description}
                  disabled={busy}
                  onChange={(e) => {
                    setFields({ ...fields, description: e.target.value })
                    setDirty(true)
                  }}
                />
              </label>
              <details className="series-selector">
                <summary>详细资料与完整性</summary>
                <div className="book-fields">
                  {extraFields.map(([key, label]) => (
                    <label key={key}>
                      <span>{label}</span>
                      <input
                        value={fields[key]}
                        disabled={busy}
                        onChange={(e) => {
                          setFields({ ...fields, [key]: e.target.value })
                          setDirty(true)
                        }}
                      />
                    </label>
                  ))}
                  <label>
                    <span>别名（每行一个）</span>
                    <textarea
                      value={fields.aliases.join('\n')}
                      disabled={busy}
                      onChange={(e) => {
                        setFields({
                          ...fields,
                          aliases: e.target.value.split('\n'),
                        })
                        setDirty(true)
                      }}
                    />
                  </label>
                  <label>
                    <span>已核实总册数（未知留空）</span>
                    <input
                      type="number"
                      min={1}
                      step={1}
                      value={fields.expected_count ?? ''}
                      disabled={busy}
                      onChange={(e) => {
                        setFields({
                          ...fields,
                          expected_count: e.target.value
                            ? Number(e.target.value)
                            : null,
                        })
                        setDirty(true)
                      }}
                    />
                  </label>
                  <label>
                    <span>成员完整性</span>
                    <select
                      value={fields.completeness}
                      disabled={busy}
                      onChange={(e) => {
                        setFields({
                          ...fields,
                          completeness: e.target
                            .value as Fields['completeness'],
                        })
                        setDirty(true)
                      }}
                    >
                      <option value="unknown">待核实</option>
                      <option value="partial">部分收录</option>
                      <option value="complete">已完整核实</option>
                    </select>
                  </label>
                </div>
                <p>
                  资料未知可留空。标记完整需要明确核实，且总册数与活动成员数一致。
                </p>
              </details>
              {series && <p className="series-scope">{coverage(series)}</p>}
              <button
                className="primary-button"
                type="submit"
                disabled={
                  busy || !fields.name.trim() || (id !== null && !dirty)
                }
              >
                {busy
                  ? '正在保存…'
                  : id === null
                    ? book?.catalog_status === 'active'
                      ? '创建并关联当前书'
                      : '创建系列'
                    : '保存系列资料'}
              </button>
            </form>
          )}
          {pane === 'members' && id !== null && (
            <>
              <h4>{series?.name} · 成员维护</h4>
              <p>
                {series && coverage(series)}
                。归档和历史成员保留状态；移出只解除本系列关系。
              </p>
              {members.length === 0 && (
                <p>尚未收录分册，请关联已有图书或添加新图书。</p>
              )}
              <ol className="series-maintenance-members">
                {members.map(({ book: member }, index) => (
                  <li key={member.id}>
                    <div>
                      <strong>{member.title}</strong>
                      <small>
                        {[member.isbn, member.edition]
                          .filter(Boolean)
                          .join(' · ') || '版本资料待核实'}
                        {member.catalog_status !== 'active'
                          ? ' · 已归档/历史成员'
                          : ''}
                      </small>
                    </div>
                    <div className="management-actions">
                      <button
                        type="button"
                        className="secondary-button"
                        aria-label={'上移 ' + member.title}
                        disabled={busy || index === 0}
                        onClick={() => move(index, -1)}
                      >
                        上移
                      </button>
                      <button
                        type="button"
                        className="secondary-button"
                        aria-label={'下移 ' + member.title}
                        disabled={busy || index === members.length - 1}
                        onClick={() => move(index, 1)}
                      >
                        下移
                      </button>
                      <button
                        type="button"
                        className="remove-button"
                        disabled={busy || orderDirty}
                        onClick={() =>
                          void run(async () => {
                            await request(
                              '/series/' + id + '/members/' + member.id,
                              {
                                method: 'DELETE',
                                headers: { 'X-CSRFToken': csrfToken() },
                              },
                            )
                          }, '已移出此系列，图书及书架记录保留。')
                        }
                      >
                        移出系列
                      </button>
                    </div>
                  </li>
                ))}
              </ol>
              {orderDirty && (
                <button
                  type="button"
                  className="primary-button"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await jsonRequest(
                        '/series/' + id + '/members/order',
                        'PUT',
                        { book_ids: members.map((m) => m.book.id) },
                      )
                      setOrderDirty(false)
                    }, '分册顺序已保存。')
                  }
                >
                  保存分册顺序
                </button>
              )}
              <h4>关联已有图书</h4>
              <label>
                <span>查找书名或单本 ISBN</span>
                <input
                  type="search"
                  value={query}
                  disabled={busy || orderDirty}
                  onChange={(e) => {
                    setQuery(e.target.value)
                    setSelected([])
                  }}
                />
              </label>
              <div className="series-choices">
                {matches.map((m) => (
                  <label key={m.id} className="inline-choice">
                    <input
                      type="checkbox"
                      checked={selected.includes(m.id)}
                      disabled={
                        busy ||
                        orderDirty ||
                        members.some((member) => member.book.id === m.id)
                      }
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, m.id]
                            : selected.filter((pk) => pk !== m.id),
                        )
                      }
                    />
                    <span>
                      {m.title}
                      <small>
                        {[m.isbn, m.edition, m.publisher]
                          .filter(Boolean)
                          .join(' · ')}
                        {members.some((member) => member.book.id === m.id)
                          ? ' · 已在系列'
                          : ''}
                      </small>
                    </span>
                  </label>
                ))}
              </div>
              <button
                type="button"
                className="primary-button"
                disabled={busy || orderDirty || selected.length === 0}
                onClick={() =>
                  void run(async () => {
                    await jsonRequest('/series/' + id + '/members', 'POST', {
                      book_ids: selected,
                    })
                    setSelected([])
                  }, '所选图书已关联到系列。')
                }
              >
                关联已选 {selected.length} 本
              </button>
            </>
          )}
          {pane === 'intake' && series && (
            <CatalogManagement
              capabilities={capabilities}
              targetSeries={series}
              onManageSeries={choose}
              onAuthRequired={onAuthRequired}
              onUnsavedChange={setIntakeDirty}
              onLibraryChanged={async () => {
                const [updated, list] = await Promise.all([
                  request<SeriesDetail>('/series/' + series.id),
                  request<{ members: Member[] }>(
                    '/series/' + series.id + '/members',
                  ),
                ])
                setSeries(updated)
                setMembers(list.members)
                await onLibraryChanged()
              }}
            />
          )}
        </>
      )}
    </section>
  )
}
