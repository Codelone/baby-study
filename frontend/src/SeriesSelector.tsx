import { useEffect, useState } from 'react'
import { jsonRequest, request, RequestError } from './api'
import type { BookSeries } from './catalog'

export default function SeriesSelector({
  value,
  onChange,
  disabled = false,
  options,
  onCreated,
  onAuthRequired,
}: {
  value: number[]
  onChange: (ids: number[]) => void
  disabled?: boolean
  options?: BookSeries[]
  onCreated?: (series: BookSeries) => void
  onAuthRequired?: () => void
}) {
  const [loaded, setLoaded] = useState<BookSeries[]>([])
  const [query, setQuery] = useState('')
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
  const [ready, setReady] = useState(options !== undefined)
  const choices = options ?? loaded
  useEffect(() => {
    if (options !== undefined) return
    const abort = new AbortController()
    request<{ series: BookSeries[] }>('/series', { signal: abort.signal })
      .then((result) => {
        if (!abort.signal.aborted) {
          setLoaded(result.series)
          setReady(true)
          setError('')
        }
      })
      .catch((cause: RequestError) => {
        if (abort.signal.aborted) return
        setError(cause.message)
        if (cause.code === 'not_authenticated') onAuthRequired?.()
      })
    return () => abort.abort()
  }, [options, version, onAuthRequired])
  const term = query.trim().toLocaleLowerCase()
  const filtered = choices.filter(
    (s) =>
      [s.name, s.family, ...s.aliases].some((text) =>
        text.toLocaleLowerCase().includes(term),
      ) ||
      (!!term.replace(/[- ]/g, '') &&
        s.isbn.includes(term.replace(/[- ]/g, ''))),
  )
  async function create() {
    setCreating(true)
    setError('')
    try {
      const series = await jsonRequest<BookSeries>('/series', 'POST', {
        name: name.trim(),
      })
      setLoaded((previous) => [...previous, series])
      onCreated?.(series)
      onChange([...new Set([...value, series.id])])
      setName('')
      setQuery('')
    } catch (cause) {
      const failure = cause as RequestError
      setError(failure.message)
      if (failure.code === 'not_authenticated') onAuthRequired?.()
    } finally {
      setCreating(false)
    }
  }
  return (
    <details className="series-selector">
      <summary>
        维护系列 · {value.length ? `已选择 ${value.length} 个` : '未选择'}
      </summary>
      <p className="question-hint">
        可以选择多个系列。此处维护归属，不代表加入个人书架。
      </p>
      {value.length > 0 && (
        <p>
          已选择：
          {value
            .map(
              (id) => choices.find((s) => s.id === id)?.name ?? `系列 #${id}`,
            )
            .join('、')}
        </p>
      )}
      {error && (
        <p role="alert" className="error-banner">
          {error}{' '}
          <button
            type="button"
            className="text-button"
            onClick={() => setVersion((v) => v + 1)}
          >
            重新读取系列
          </button>
        </p>
      )}
      <label>
        <span>查找系列</span>
        <input
          type="search"
          placeholder="系列名称、别名或套装 ISBN"
          value={query}
          disabled={disabled || creating}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      {!ready && options === undefined && <p role="status">正在读取系列…</p>}
      <div className="series-choices">
        {filtered.map((s) => (
          <label className="inline-choice" key={s.id}>
            <input
              type="checkbox"
              checked={value.includes(s.id)}
              disabled={disabled || creating}
              onChange={(e) =>
                onChange(
                  e.target.checked
                    ? [...value, s.id]
                    : value.filter((id) => id !== s.id),
                )
              }
            />
            <span>
              {s.name}
              <small>
                {[s.edition, s.publisher, s.isbn].filter(Boolean).join(' · ') ||
                  '版本资料待补全'}
              </small>
            </span>
          </label>
        ))}
        {ready && filtered.length === 0 && (
          <p>没有匹配的系列，可以在下面新建。</p>
        )}
      </div>
      <div className="inline-series-create">
        <label>
          <span>新建系列名称</span>
          <input
            value={name}
            placeholder="例如：某系列第一阶·点读版"
            disabled={disabled || creating}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="secondary-button"
          disabled={disabled || creating || !name.trim()}
          onClick={() => void create()}
        >
          {creating ? '正在新建…' : '新建并选择系列'}
        </button>
      </div>
    </details>
  )
}
