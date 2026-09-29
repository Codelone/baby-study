import { useEffect, useState } from 'react'
import AssessmentArea from './Assessment'
import CatalogDetails, { Cover } from './CatalogDetails'
import { coverage, languageLabels } from './catalog'
import type { Book, BookSeries, Language } from './catalog'
import { jsonRequest, request, RequestError } from './api'

type ReadingStatus = 'unread' | 'reading' | 'familiar' | 'paused'
type Section = 'shelf' | 'catalog' | 'assessment'

type ShelfItem = {
  id: number
  book: Book
  ownership_status: 'owned'
  reading_status: ReadingStatus
}

type SeriesAddResult = {
  added_count: number
  already_owned_count: number
  total_count: number
}

const readingLabels: Record<ReadingStatus, string> = {
  unread: '未读',
  reading: '近期在读',
  familiar: '熟悉',
  paused: '暂停',
}

const readingOptions = Object.entries(readingLabels) as [
  ReadingStatus,
  string,
][]

function BookDetails({
  book,
  onDetails,
}: {
  book: Book
  onDetails: (book: Book) => void
}) {
  return (
    <>
      <div className="book-identity">
        <Cover url={book.cover_url} title={book.title} />
        <div>
          <h3>{book.title}</h3>
          <p className="book-meta">
            {book.authors.join('、') || book.publisher || '书目资料待补全'}
          </p>
          <button
            type="button"
            className="text-button"
            onClick={(event) => {
              event.currentTarget.focus()
              onDetails(book)
            }}
            aria-label={'查看' + book.title + '详情'}
          >
            查看详情 →
          </button>
        </div>
      </div>
      {book.series_ids.length > 1 && (
        <p className="shared-note">同一本书，阅读状态共享</p>
      )}
      <p className="book-meta">
        <span>{languageLabels[book.language]}</span>
        {book.series_name && <span>系列：{book.series_name}</span>}
      </p>
      {book.series_description && (
        <p className="series-description">{book.series_description}</p>
      )}
    </>
  )
}

function AddBookButton({
  book,
  disabled,
  onAdd,
}: {
  book: Book
  disabled: boolean
  onAdd: (book: Book) => void
}) {
  return (
    <button
      className={book.is_on_shelf ? 'added-button' : 'add-button'}
      type="button"
      disabled={book.is_on_shelf || disabled}
      onClick={() => onAdd(book)}
    >
      {book.is_on_shelf
        ? '已在书架'
        : book.series_id !== null
          ? '加入这一本'
          : '加入书架'}
    </button>
  )
}

function loadLibrary(query: string, language: Language | '') {
  const params = new URLSearchParams()
  if (query.trim()) params.set('q', query.trim())
  if (language) params.set('language', language)
  return Promise.all([
    request<{ books: Book[]; series: BookSeries[] }>(
      '/books?' + params.toString(),
    ),
    request<{ items: ShelfItem[] }>('/shelf'),
  ])
}

export default function App() {
  const [detailTarget, setDetailTarget] = useState<{
    kind: 'book' | 'series'
    id: number
  } | null>(null)
  const showBook = (book: Book) =>
    setDetailTarget({ kind: 'book', id: book.id })
  const [section, setSection] = useState<Section>('shelf')
  const [books, setBooks] = useState<Book[]>([])
  const [series, setSeries] = useState<BookSeries[]>([])
  const [shelf, setShelf] = useState<ShelfItem[]>([])
  const [query, setQuery] = useState('')
  const [language, setLanguage] = useState<Language | ''>('')
  const [loading, setLoading] = useState(true)
  const [authRequired, setAuthRequired] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(
    {},
  )
  const disabled = loading || busyAction !== null

  useEffect(() => {
    let active = true
    setLoading(true)
    loadLibrary(query, language)
      .then(([bookData, shelfData]) => {
        if (!active) return
        setBooks(bookData.books)
        setSeries(bookData.series)
        setShelf(shelfData.items)
        setError('')
        setAuthRequired(false)
      })
      .catch((cause: RequestError) => {
        if (!active) return
        setAuthRequired(cause.code === 'not_authenticated')
        setError(cause.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [query, language])

  async function refreshLibrary() {
    const [bookData, shelfData] = await loadLibrary(query, language)
    setBooks(bookData.books)
    setSeries(bookData.series)
    setShelf(shelfData.items)
    setAuthRequired(false)
  }

  function showFailure(cause: unknown) {
    const failure = cause as RequestError
    setAuthRequired(failure.code === 'not_authenticated')
    setError(failure.message)
  }

  async function saveShelf(
    bookId: number,
    ownership: 'owned' | 'not_owned',
    reading: ReadingStatus | null,
  ) {
    setBusyAction('book:' + bookId)
    setError('')
    setNotice('')
    try {
      await jsonRequest('/shelf/' + bookId, 'PUT', {
        ownership_status: ownership,
        reading_status: reading,
      })
      await refreshLibrary()
    } catch (cause) {
      showFailure(cause)
    } finally {
      setBusyAction(null)
    }
  }

  async function addSeries(group: BookSeries) {
    setBusyAction('series:' + group.id)
    setError('')
    setNotice('')
    try {
      const result = await jsonRequest<SeriesAddResult>(
        '/shelf/series/' + group.id,
        'POST',
        {},
      )
      setNotice(
        `“${group.name}”本次加入 ${result.added_count} 本，已有 ${result.already_owned_count} 本保留原状态。`,
      )
      await refreshLibrary()
    } catch (cause) {
      showFailure(cause)
    } finally {
      setBusyAction(null)
    }
  }

  function setExpanded(key: string, open: boolean) {
    setExpandedGroups((previous) =>
      previous[key] === open ? previous : { ...previous, [key]: open },
    )
  }

  function renderShelfBook(item: ShelfItem) {
    return (
      <li className="book-card" key={item.id}>
        <BookDetails book={item.book} onDetails={showBook} />
        <div className="card-actions">
          <label>
            <span>阅读状态</span>
            <select
              value={item.reading_status}
              disabled={disabled}
              aria-label={item.book.title + '的阅读状态'}
              onChange={(event) =>
                void saveShelf(
                  item.book.id,
                  'owned',
                  event.target.value as ReadingStatus,
                )
              }
            >
              {readingOptions.map(([value, label]) => (
                <option value={value} key={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button
            className="remove-button"
            type="button"
            disabled={disabled}
            onClick={() => void saveShelf(item.book.id, 'not_owned', null)}
          >
            移出书架
          </button>
        </div>
      </li>
    )
  }

  const normalizedQuery = query.trim().toLocaleLowerCase()
  const visibleBooks = books.filter(
    (book) =>
      book.series_ids.length === 0 ||
      (normalizedQuery &&
        [book.title, book.original_title, ...book.aliases].some((name) =>
          name.toLocaleLowerCase().includes(normalizedQuery),
        )),
  )
  const shelfGroups = new Map<
    number,
    { id: number; name: string; items: ShelfItem[] }
  >()
  const independentShelf: ShelfItem[] = []
  for (const item of shelf) {
    if (item.book.series.length === 0) independentShelf.push(item)
    for (const groupInfo of item.book.series) {
      const group = shelfGroups.get(groupInfo.id) ?? {
        id: groupInfo.id,
        name: groupInfo.name,
        items: [],
      }
      group.items.push(item)
      shelfGroups.set(groupInfo.id, group)
    }
  }

  return (
    <main className="app-shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">BABY STUDY · 家庭阅读</p>
          <h1>家里的每一本书，都有自己的位置。</h1>
          <p className="subtitle">
            整理家中读物，记录孩子的真实表现，找到适合的共读起点。
          </p>
        </div>
        <a className="admin-link" href="/admin/catalog/book/">
          管理图书清单 <span aria-hidden="true">↗</span>
        </a>
      </header>

      {authRequired ? (
        <section className="message-panel" aria-live="polite">
          <h2>请先登录</h2>
          <p>使用管理员账户登录后，就可以整理家庭书架。</p>
          <a className="primary-link" href="/admin/login/?next=/">
            前往登录
          </a>
        </section>
      ) : (
        <>
          <nav className="tabs" aria-label="主要导航">
            <button
              className={section === 'shelf' ? 'tab active' : 'tab'}
              type="button"
              onClick={() => setSection('shelf')}
            >
              我的书架 <span>{shelf.length}</span>
            </button>
            <button
              className={section === 'catalog' ? 'tab active' : 'tab'}
              type="button"
              onClick={() => setSection('catalog')}
            >
              系统书目 <span>{books.length} 本</span>
            </button>
            <button
              className={section === 'assessment' ? 'tab active' : 'tab'}
              type="button"
              onClick={() => setSection('assessment')}
            >
              初始评估
            </button>
          </nav>

          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {notice && (
            <p className="success-banner" role="status">
              {notice}
            </p>
          )}

          {section === 'assessment' ? (
            <AssessmentArea
              shelfCount={shelf.length}
              onGoShelf={() => setSection('shelf')}
              onAuthRequired={() => setAuthRequired(true)}
            />
          ) : section === 'shelf' ? (
            <section aria-labelledby="shelf-heading">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">HOME SHELF</p>
                  <h2 id="shelf-heading">我的书架</h2>
                </div>
                <button
                  className="text-button"
                  type="button"
                  onClick={() => setSection('catalog')}
                >
                  去选书 →
                </button>
              </div>
              {loading ? (
                <p className="empty-state">正在读取书架…</p>
              ) : shelf.length === 0 ? (
                <div className="empty-state">
                  <p>书架还是空的。</p>
                  <button
                    className="primary-button"
                    type="button"
                    onClick={() => setSection('catalog')}
                  >
                    从系统书目选书
                  </button>
                </div>
              ) : (
                <>
                  <div className="series-list">
                    {[...shelfGroups.values()]
                      .sort((left, right) =>
                        left.name.localeCompare(right.name, 'zh-CN'),
                      )
                      .map((group) => (
                        <details
                          className="shelf-series"
                          key={group.id}
                          open={expandedGroups['shelf:' + group.id] ?? false}
                          onToggle={(event) =>
                            setExpanded(
                              'shelf:' + group.id,
                              event.currentTarget.open,
                            )
                          }
                        >
                          <summary>
                            <span className="series-title">{group.name}</span>
                            <span className="series-count">
                              已拥有 {group.items.length} 本
                            </span>
                          </summary>
                          <ul className="book-grid">
                            {group.items.map(renderShelfBook)}
                          </ul>
                        </details>
                      ))}
                  </div>
                  {independentShelf.length > 0 && (
                    <>
                      <h3 className="list-heading">独立图书</h3>
                      <ul className="book-grid">
                        {independentShelf.map(renderShelfBook)}
                      </ul>
                    </>
                  )}
                </>
              )}
            </section>
          ) : (
            <section aria-labelledby="catalog-heading">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">BOOK CATALOG</p>
                  <h2 id="catalog-heading">系统书目</h2>
                </div>
                <p>按单本记录，也可以批量加入系列已收录的分册。</p>
              </div>
              <div className="filters">
                <label>
                  <span>查找书名或系列</span>
                  <input
                    type="search"
                    value={query}
                    disabled={busyAction !== null}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="输入书名或系列名称"
                  />
                </label>
                <label>
                  <span>语言</span>
                  <select
                    value={language}
                    disabled={busyAction !== null}
                    onChange={(event) =>
                      setLanguage(event.target.value as Language | '')
                    }
                  >
                    <option value="">全部语言</option>
                    {Object.entries(languageLabels).map(([value, label]) => (
                      <option value={value} key={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {loading ? (
                <p className="empty-state">正在查找图书…</p>
              ) : visibleBooks.length === 0 && series.length === 0 ? (
                <p className="empty-state">没有找到符合条件的图书或系列。</p>
              ) : (
                <>
                  {visibleBooks.length > 0 && (
                    <>
                      <h3 className="list-heading">
                        {normalizedQuery ? '匹配的单本' : '独立图书'}
                      </h3>
                      <ul className="book-grid">
                        {visibleBooks.map((book) => (
                          <li className="book-card" key={book.id}>
                            <BookDetails book={book} onDetails={showBook} />
                            <AddBookButton
                              book={book}
                              disabled={disabled}
                              onAdd={(selected) =>
                                void saveShelf(selected.id, 'owned', 'unread')
                              }
                            />
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                  {series.length > 0 && (
                    <>
                      <h3 className="list-heading">系列图书</h3>
                      <div className="series-list">
                        {series.map((group) => {
                          const complete =
                            group.total_count > 0 &&
                            group.owned_count === group.total_count
                          const missing = group.total_count - group.owned_count
                          return (
                            <article className="series-card" key={group.id}>
                              <div className="series-card-heading">
                                <div>
                                  <div className="book-identity">
                                    <Cover
                                      url={group.cover_url}
                                      title={group.name}
                                    />
                                    <div>
                                      <h3>{group.name}</h3>
                                      <button
                                        type="button"
                                        className="text-button"
                                        onClick={(event) => {
                                          event.currentTarget.focus()
                                          setDetailTarget({
                                            kind: 'series',
                                            id: group.id,
                                          })
                                        }}
                                      >
                                        查看套装详情 →
                                      </button>
                                    </div>
                                  </div>
                                  <p className="book-meta">
                                    <span>{coverage(group)}</span>
                                    <span>已拥有 {group.owned_count} 本</span>
                                    {[
                                      ...new Set(
                                        group.books.map(
                                          (book) => book.language,
                                        ),
                                      ),
                                    ].map((value) => (
                                      <span key={value}>
                                        {languageLabels[value]}
                                      </span>
                                    ))}
                                  </p>
                                </div>
                                <button
                                  className={
                                    complete ? 'added-button' : 'add-button'
                                  }
                                  type="button"
                                  disabled={
                                    complete ||
                                    disabled ||
                                    group.total_count === 0
                                  }
                                  onClick={() => void addSeries(group)}
                                >
                                  {busyAction === 'series:' + group.id
                                    ? '正在加入…'
                                    : group.total_count === 0
                                      ? '尚未收录分册'
                                      : complete
                                        ? '已收录分册均在书架'
                                        : group.completeness !== 'complete'
                                          ? `加入已收录 ${group.total_count} 本`
                                          : group.owned_count > 0
                                            ? `补齐系列 · ${missing} 本`
                                            : `加入整个系列 · ${group.total_count} 本`}
                                </button>
                              </div>
                              {group.description && (
                                <p className="series-description">
                                  {group.description}
                                </p>
                              )}
                              <p className="series-scope">
                                加入范围：已收录的 {group.total_count} 本
                                {language ? '，不受语言筛选影响' : ''}
                                。已拥有的书会保留阅读状态。
                              </p>
                              <details
                                className="series-members"
                                open={
                                  expandedGroups['catalog:' + group.id] ?? false
                                }
                                onToggle={(event) =>
                                  setExpanded(
                                    'catalog:' + group.id,
                                    event.currentTarget.open,
                                  )
                                }
                              >
                                <summary>
                                  查看已收录 {group.total_count}{' '}
                                  本，也可逐本加入
                                </summary>
                                <ul className="book-grid">
                                  {group.books.map((book) => (
                                    <li className="book-card" key={book.id}>
                                      <BookDetails
                                        book={book}
                                        onDetails={showBook}
                                      />
                                      <AddBookButton
                                        book={book}
                                        disabled={disabled}
                                        onAdd={(selected) =>
                                          void saveShelf(
                                            selected.id,
                                            'owned',
                                            'unread',
                                          )
                                        }
                                      />
                                    </li>
                                  ))}
                                </ul>
                              </details>
                            </article>
                          )
                        })}
                      </div>
                    </>
                  )}
                </>
              )}
            </section>
          )}
        </>
      )}
      {detailTarget && (
        <CatalogDetails
          target={detailTarget}
          onClose={() => setDetailTarget(null)}
          onBook={(id) => setDetailTarget({ kind: 'book', id })}
        />
      )}
    </main>
  )
}
