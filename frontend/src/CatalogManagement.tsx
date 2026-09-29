import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { csrfToken, jsonRequest, request, RequestError } from './api'
import BookFieldsEditor from './BookFieldsEditor'
import IntakeReview from './IntakeReview'
import {
  cleanFields,
  emptyFields,
  intakeIdFromHash,
  intakeLabels,
  isRunning,
} from './intake'
import type { BookDetail } from './catalog'
import type { BookFields, CatalogCapabilities, Intake } from './intake'

type Pane = 'manual' | 'photos' | 'tasks'

function FilePreview({ file }: { file: File }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    const objectUrl = URL.createObjectURL(file)
    setUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [file])
  return <img src={url || undefined} alt={'待上传照片：' + file.name} />
}

export default function CatalogManagement({
  capabilities,
  onLibraryChanged,
  onAuthRequired,
  onUnsavedChange,
}: {
  capabilities: CatalogCapabilities
  onLibraryChanged: () => Promise<void>
  onAuthRequired: () => void
  onUnsavedChange: (dirty: boolean) => void
}) {
  const initialTaskId = intakeIdFromHash()
  const [pane, setPane] = useState<Pane>(initialTaskId ? 'tasks' : 'photos')
  const [fields, setFields] = useState<BookFields>(emptyFields)
  const [manualDirty, setManualDirty] = useState(false)
  const [reviewDirty, setReviewDirty] = useState(false)
  const [addToShelf, setAddToShelf] = useState(false)
  const idempotency = useRef(crypto.randomUUID())
  const [files, setFiles] = useState<File[]>([])
  const [mode, setMode] = useState<'batch' | 'realtime'>('batch')
  const [tasks, setTasks] = useState<Intake[]>([])
  const [tasksLoaded, setTasksLoaded] = useState(false)
  const [selectedId, setSelectedId] = useState(initialTaskId)
  const [detail, setDetail] = useState<Intake | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [taskError, setTaskError] = useState('')
  const [listVersion, setListVersion] = useState(0)
  const [detailVersion, setDetailVersion] = useState(0)
  const formRef = useRef<HTMLFormElement>(null)
  const manualCompleted = useRef(false)
  const unsaved = manualDirty || reviewDirty || files.length > 0
  const configured =
    mode === 'batch'
      ? capabilities.batch_configured
      : capabilities.realtime_configured

  useEffect(() => {
    if (!busy && manualCompleted.current) {
      manualCompleted.current = false
      formRef.current?.querySelector<HTMLInputElement>('input')?.focus()
    }
  }, [busy])

  useEffect(() => {
    onUnsavedChange(unsaved)
    return () => onUnsavedChange(false)
  }, [unsaved, onUnsavedChange])
  useEffect(() => {
    if (!manualDirty && !files.length) return
    function preventLeave(event: BeforeUnloadEvent) {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', preventLeave)
    return () => window.removeEventListener('beforeunload', preventLeave)
  }, [manualDirty, files.length])

  const updateTask = useCallback((intake: Intake) => {
    setTasks((previous) =>
      [intake, ...previous.filter((task) => task.id !== intake.id)].sort(
        (a, b) => b.created_at.localeCompare(a.created_at),
      ),
    )
  }, [])

  useEffect(() => {
    const abort = new AbortController()
    let timer: number | undefined
    function loadTasks() {
      request<{ intakes: Intake[] }>('/book-intakes', { signal: abort.signal })
        .then((result) => {
          if (abort.signal.aborted) return
          setTasks(result.intakes)
          setTasksLoaded(true)
          setTaskError('')
          if (result.intakes.some(isRunning))
            timer = window.setTimeout(loadTasks, 8000)
        })
        .catch((cause: RequestError) => {
          if (abort.signal.aborted) return
          setTaskError('读取录入任务失败：' + cause.message)
          if (cause.code === 'not_authenticated') onAuthRequired()
          timer = window.setTimeout(loadTasks, 15000)
        })
    }
    loadTasks()
    return () => {
      abort.abort()
      window.clearTimeout(timer)
    }
  }, [listVersion, onAuthRequired])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    const abort = new AbortController()
    setDetail(null)
    setError('')
    request<{ intake: Intake }>('/book-intakes/' + selectedId, {
      signal: abort.signal,
    })
      .then((result) => {
        if (!abort.signal.aborted) {
          setDetail(result.intake)
          updateTask(result.intake)
        }
      })
      .catch((cause: RequestError) => {
        if (abort.signal.aborted) return
        setError('读取任务失败：' + cause.message)
        if (cause.code === 'not_authenticated') onAuthRequired()
      })
    return () => abort.abort()
  }, [selectedId, detailVersion, onAuthRequired, updateTask])

  function switchPane(next: Pane) {
    if (busy) return
    if (
      reviewDirty &&
      next !== pane &&
      !window.confirm('草稿有未保存修改。离开核对页会放弃这些修改，确认离开？')
    )
      return
    setPane(next)
    setError('')
    setNotice('')
    if (next !== 'tasks') {
      setSelectedId('')
      window.history.replaceState(null, '', '#catalog/manage')
    }
  }
  function selectTask(id: string) {
    if (id === selectedId && detail) return
    if (
      reviewDirty &&
      id !== selectedId &&
      !window.confirm(
        '当前草稿有未保存修改。切换任务会放弃这些修改，确认切换？',
      )
    )
      return
    setPane('tasks')
    setSelectedId(id)
    setDetailVersion((value) => value + 1)
    window.history.replaceState(null, '', '#catalog/intakes/' + id)
  }
  function showFailure(cause: unknown) {
    const failure = cause as RequestError
    setError(failure.message || '请求失败，请稍后重试。')
    if (failure.code === 'not_authenticated') onAuthRequired()
  }
  function addFiles(incoming: FileList | null) {
    if (!incoming) return
    const accepted: File[] = []
    const issues: string[] = []
    for (const file of Array.from(incoming)) {
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
        issues.push(file.name + '：请使用 JPEG、PNG 或 WebP 照片。')
        continue
      }
      if (file.size > 8 * 1024 * 1024) {
        issues.push(file.name + '：照片超过 8 MiB。')
        continue
      }
      if (file.size === 0) {
        issues.push(file.name + '：照片文件为空。')
        continue
      }
      accepted.push(file)
    }
    setFiles((previous) => {
      const next = [...previous]
      accepted.forEach((file) => {
        if (
          !next.some(
            (item) =>
              item.name === file.name &&
              item.size === file.size &&
              item.lastModified === file.lastModified,
          )
        )
          next.push(file)
      })
      return next
    })
    setError(issues.join(' '))
    setNotice('')
  }
  async function manualSubmit(event: FormEvent) {
    event.preventDefault()
    setBusy('manual')
    setError('')
    setNotice('')
    try {
      const result = await jsonRequest<{ book: BookDetail; created: boolean }>(
        '/books',
        'POST',
        {
          fields: cleanFields(fields),
          idempotency_key: idempotency.current,
          add_to_shelf: addToShelf,
        },
      )
      setFields(emptyFields())
      setManualDirty(false)
      manualCompleted.current = true
      idempotency.current = crypto.randomUUID()
      setNotice(
        '“' +
          result.book.title +
          '”' +
          (result.created ? '已加入系统书目' : '已复用已有书目') +
          (addToShelf ? '，并已加入我的书架。' : '。'),
      )
      try {
        await onLibraryChanged()
      } catch {
        setNotice(
          '图书已保存，刷新书目暂时失败。请刷新页面查看；无需重复新增。',
        )
      }
    } catch (cause) {
      showFailure(cause)
    } finally {
      setBusy('')
    }
  }
  async function upload(event: FormEvent) {
    event.preventDefault()
    if (!files.length || files.length > 30 || !configured) return
    setBusy('upload')
    setError('')
    setNotice('')
    try {
      const body = new FormData()
      files.forEach((file) => body.append('files', file))
      body.append('mode', mode)
      const result = await request<{ intake: Intake }>('/book-intakes', {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken() },
        body,
      })
      setFiles([])
      updateTask(result.intake)
      selectTask(result.intake.id)
      setListVersion((value) => value + 1)
      setNotice('照片已上传，任务已保存。识别后请核对分组并统一确认入库。')
    } catch (cause) {
      showFailure(cause)
    } finally {
      setBusy('')
    }
  }
  return (
    <section
      className="catalog-management"
      aria-labelledby="catalog-management-heading"
    >
      <div className="intake-group-heading">
        <h3 id="catalog-management-heading">新增图书</h3>
        <span className="status-tag">管理员</span>
      </div>
      <div className="management-tabs" role="group" aria-label="图书录入方式">
        {(
          [
            ['photos', '拍照 / 上传'],
            ['manual', '手工新增'],
            ['tasks', '录入任务'],
          ] as const
        ).map(([value, label]) => (
          <button
            type="button"
            className={pane === value ? 'tab active' : 'tab'}
            aria-pressed={pane === value}
            key={value}
            disabled={!!busy}
            onClick={() => switchPane(value)}
          >
            {label}
          </button>
        ))}
      </div>
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
      {pane === 'manual' && (
        <form
          ref={formRef}
          onSubmit={(event) => void manualSubmit(event)}
          aria-busy={busy === 'manual'}
        >
          <p className="question-hint">
            填写实际单本的资料，未知项留空。新增后可从图书详情归档。
          </p>
          <BookFieldsEditor
            fields={fields}
            disabled={!!busy}
            onField={(key, value) => {
              setFields((previous) => ({ ...previous, [key]: value }))
              setManualDirty(true)
            }}
          />
          <label className="inline-choice">
            <input
              type="checkbox"
              checked={addToShelf}
              disabled={!!busy}
              onChange={(event) => setAddToShelf(event.target.checked)}
            />
            同时加入我的书架
          </label>
          <div className="management-actions">
            <button
              className="primary-button"
              type="submit"
              disabled={!!busy || !fields.title.trim()}
            >
              {busy === 'manual' ? '正在保存…' : '确认新增图书'}
            </button>
          </div>
        </form>
      )}
      {pane === 'photos' && (
        <form
          onSubmit={(event) => void upload(event)}
          aria-busy={busy === 'upload'}
        >
          <p className="question-hint">
            不同书的照片可以一起上传，系统会自动分组。每张主要拍一本书，建议同时拍清封面与版权页；识别后仍需核对。
          </p>
          <div className="photo-upload-controls">
            <label className="file-picker">
              <span>手机拍照</span>
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                capture="environment"
                disabled={!!busy}
                onChange={(event) => {
                  addFiles(event.target.files)
                  event.target.value = ''
                }}
              />
            </label>
            <label className="file-picker">
              <span>选择照片（可多选）</span>
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                disabled={!!busy}
                onChange={(event) => {
                  addFiles(event.target.files)
                  event.target.value = ''
                }}
              />
            </label>
          </div>
          <p className="book-meta">
            每次最多 30 张，每张不超过 8 MiB，支持 JPEG / PNG /
            WebP。原始照片仅任务所有者可查看，通常保留 30 天。
          </p>
          {files.length > 0 && (
            <>
              <p role="status">
                待上传 {files.length} 张
                {files.length > 30 ? '，请移除多余照片。' : ''}
              </p>
              <ul className="intake-photos upload-previews">
                {files.map((file, index) => (
                  <li
                    className="intake-photo"
                    key={file.name + file.size + file.lastModified}
                  >
                    <FilePreview file={file} />
                    <span>{file.name}</span>
                    <button
                      className="remove-button"
                      type="button"
                      disabled={!!busy}
                      onClick={() =>
                        setFiles((previous) =>
                          previous.filter((_, position) => position !== index),
                        )
                      }
                      aria-label={'移除待上传照片 ' + file.name}
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          <fieldset className="choice-group" disabled={!!busy}>
            <legend>识别方式</legend>
            <label className="choice">
              <input
                type="radio"
                name="intake-mode"
                value="batch"
                checked={mode === 'batch'}
                onChange={() => setMode('batch')}
              />
              <span>
                省钱 Batch（默认） · 可以等待，完成时间取决于调度
                {!capabilities.batch_configured ? ' · 尚未配置' : ''}
              </span>
            </label>
            <label className="choice">
              <input
                type="radio"
                name="intake-mode"
                value="realtime"
                checked={mode === 'realtime'}
                onChange={() => setMode('realtime')}
              />
              <span>
                实时识别 · 更快开始处理
                {!capabilities.realtime_configured ? ' · 尚未配置' : ''}
              </span>
            </label>
          </fieldset>
          {!configured && (
            <p className="config-notice" role="status">
              {mode === 'batch' ? 'Batch' : '实时'}
              识别尚未配置。可选择已配置的方式，或使用手工新增；不会自动切换识别方式。
            </p>
          )}
          <button
            className="primary-button"
            type="submit"
            disabled={
              !!busy || !files.length || files.length > 30 || !configured
            }
          >
            {busy === 'upload' ? '正在上传…' : '上传照片并创建识别任务'}
          </button>
        </form>
      )}
      {pane === 'tasks' && (
        <>
          <div className="intake-group-heading">
            <p className="question-hint">
              任务保存在你的账户中，刷新后可继续查看；识别结束后核对分组、疑点和来源，再确认入库。
            </p>
            <button
              className="text-button"
              type="button"
              onClick={() => setListVersion((value) => value + 1)}
            >
              刷新列表
            </button>
          </div>
          {taskError && (
            <p className="error-banner" role="alert">
              {taskError}
            </p>
          )}
          {!tasksLoaded ? (
            <p role="status">正在读取任务…</p>
          ) : tasks.length === 0 ? (
            <p className="question-hint">还没有照片录入任务。</p>
          ) : (
            <ul className="intake-task-list">
              {tasks.map((task) => (
                <li key={task.id}>
                  <button
                    type="button"
                    className={
                      task.id === selectedId
                        ? 'intake-task active'
                        : 'intake-task'
                    }
                    aria-pressed={task.id === selectedId}
                    onClick={() => selectTask(task.id)}
                  >
                    <strong>{intakeLabels[task.status]}</strong>
                    <span>
                      {new Date(task.created_at).toLocaleString('zh-CN')} ·{' '}
                      {task.mode === 'batch' ? 'Batch' : '实时'} ·{' '}
                      {task.photos.length} 张 / {task.groups.length} 组
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {selectedId && !detail && !error && (
            <p role="status">正在读取任务详情…</p>
          )}
          {detail && (
            <IntakeReview
              key={detail.id}
              initial={detail}
              onIntake={updateTask}
              onConfirmed={onLibraryChanged}
              onAuthRequired={onAuthRequired}
              onDirty={setReviewDirty}
            />
          )}
        </>
      )}
    </section>
  )
}
