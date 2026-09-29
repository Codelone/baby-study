import { useEffect, useRef, useState } from 'react'
import BookFieldsEditor from './BookFieldsEditor'
import { jsonRequest, request, RequestError } from './api'
import { languageLabels } from './catalog'
import {
  canEdit,
  cleanFields,
  emptyFields,
  fieldLabels,
  fieldValue,
  intakeLabels,
  isRunning,
  issueMessage,
} from './intake'
import type {
  BookFields,
  Candidate,
  CandidateResult,
  Intake,
  IntakeEvidence,
  IntakeGroup,
  IntakePhoto,
} from './intake'

const photoLabels: Record<string, string> = {
  cover: '封面',
  front_cover: '封面',
  back_cover: '封底',
  copyright: '版权页',
  copyright_page: '版权页',
  title_page: '扉页',
  spine: '书脊',
  interior: '内页',
  content: '内页',
  unknown: '类型待核对',
  other: '其他照片',
}

function UsageSummary({ intake }: { intake: Intake }) {
  const { photos, cost_estimate: estimate } = intake
  const rawAmount = estimate?.amount
  const amount =
    typeof rawAmount === 'string' && /^\d+(?:\.\d+)?$/.test(rawAmount)
      ? Number(rawAmount)
      : null
  const formattedAmount =
    amount !== null && Number.isFinite(amount)
      ? amount.toLocaleString('zh-CN', {
          useGrouping: false,
          minimumFractionDigits: 6,
          maximumFractionDigits: 8,
        })
      : null
  const metrics = [
    ['prompt_tokens', '输入 tokens'],
    ['completion_tokens', '输出 tokens'],
    ['total_tokens', '合计 tokens'],
  ] as const
  const models = [
    ...new Set(
      photos.map((photo) => photo.extraction?.model_name).filter(Boolean),
    ),
  ]
  return (
    <section aria-label="供应商已返回用量">
      <h4>供应商已返回用量（tokens）</h4>
      <dl className="metadata-grid">
        {metrics.map(([key, label]) => {
          const returned = photos.flatMap((photo) => {
            const value = photo.extraction?.usage?.[key]
            return typeof value === 'number' &&
              Number.isFinite(value) &&
              value >= 0
              ? [value]
              : []
          })
          return (
            <div key={key}>
              <dt>{label}</dt>
              <dd>
                {returned.length
                  ? returned
                      .reduce((total, value) => total + value, 0)
                      .toLocaleString('zh-CN')
                  : '暂无返回'}
              </dd>
              <small>
                已返回 {returned.length}/{photos.length} 张照片
              </small>
            </div>
          )
        })}
      </dl>
      {models.length > 0 && (
        <p className="book-meta">返回的模型：{models.join('、')}</p>
      )}
      <p className="question-hint">
        仅汇总照片结果中已返回的用量，暂无数据不代表零消耗。失败项、未完成项及重试等调用的已消耗用量可能未包含在内；费用以
        MiMo 账单为准。
      </p>
      <div className="config-notice">
        <p>
          <strong>
            已返回用量的费用估算：
            {formattedAmount === null
              ? '暂无可估算用量'
              : '约 ¥' + formattedAmount}
          </strong>
        </p>
        {estimate && (
          <p>
            仅覆盖 {estimate.priced_photos}/{estimate.total_photos} 张照片 ·{' '}
            {estimate.mode === 'batch' ? 'Batch' : '实时'} · 价格日期{' '}
            {estimate.pricing_date}{' '}
            {/^https:\/\//i.test(estimate.source_url) && (
              <a href={estimate.source_url} target="_blank" rel="noreferrer">
                官方价格说明 ↗
              </a>
            )}
          </p>
        )}
        <p>
          输入按未命中缓存计算，费用估算仅包含上述覆盖项。失败项、未完成项及重试等调用的消耗可能未计入；此估算不是最终账单，费用以
          MiMo 账单为准。
        </p>
      </div>
    </section>
  )
}

function EvidenceList({
  evidence,
  photos = [],
}: {
  evidence: IntakeEvidence[]
  photos?: IntakePhoto[]
}) {
  return evidence.length ? (
    <ul className="evidence-list">
      {evidence.map((item, index) => (
        <li key={index}>
          <span>
            {fieldLabels[item.field as keyof BookFields] || item.field}：
            {fieldValue(item.value)}
          </span>
          {item.kind === 'photo' &&
          photos.find((photo) => photo.id === item.photo_id)?.url ? (
            <a
              href={photos.find((photo) => photo.id === item.photo_id)?.url}
              target="_blank"
              rel="noreferrer"
            >
              {item.source_label || '照片识别'} ·{' '}
              {
                photos.find((photo) => photo.id === item.photo_id)
                  ?.original_name
              }{' '}
              ↗
            </a>
          ) : item.kind === 'web' &&
            /^https?:\/\//i.test(item.source_url || '') ? (
            <a href={item.source_url} target="_blank" rel="noreferrer">
              {item.source_label || '网页来源'} ↗
            </a>
          ) : (
            <small>
              {item.kind === 'manual'
                ? '人工修改，待核对'
                : item.source_label || '照片识别，待核对'}
            </small>
          )}
        </li>
      ))}
    </ul>
  ) : (
    <p className="book-meta">暂无来源，请核对照片或保留未知值。</p>
  )
}

function Issues({ issues }: { issues: Parameters<typeof issueMessage>[0][] }) {
  return (
    issues.length > 0 && (
      <ul className="intake-issues">
        {issues.map((issue, index) => (
          <li key={index}>{issueMessage(issue)}</li>
        ))}
      </ul>
    )
  )
}

function CandidateCard({
  candidate,
  disabled,
  onApply,
}: {
  candidate: Candidate
  disabled: boolean
  onApply: (fields: Partial<BookFields>, evidence: IntakeEvidence[]) => void
}) {
  const [chosen, setChosen] = useState<(keyof BookFields)[]>([])
  const entries = (Object.keys(fieldLabels) as (keyof BookFields)[]).filter(
    (key) => {
      const value = candidate.fields[key]
      return (
        value !== undefined &&
        value !== null &&
        value !== '' &&
        (!Array.isArray(value) || value.length > 0)
      )
    },
  )
  return (
    <article className="candidate-card">
      <h4>{candidate.fields.title || '版本候选'}</h4>
      <p>
        核对
        ISBN、版本和来源后，勾选要应用的字段。已填写资料也会按你的选择替换。
      </p>
      <Issues issues={candidate.issues || []} />
      <fieldset className="candidate-fields" disabled={disabled}>
        <legend>选择候选字段</legend>
        {entries.map((key) => (
          <label className="inline-choice" key={key}>
            <input
              type="checkbox"
              checked={chosen.includes(key)}
              onChange={(event) =>
                setChosen((previous) =>
                  event.target.checked
                    ? [...previous, key]
                    : previous.filter((value) => value !== key),
                )
              }
            />
            <span>
              {fieldLabels[key]}：
              {key === 'language'
                ? languageLabels[candidate.fields.language || 'unknown']
                : fieldValue(candidate.fields[key])}
            </span>
          </label>
        ))}
      </fieldset>
      <EvidenceList evidence={candidate.evidence || []} />
      <button
        className="primary-button"
        type="button"
        disabled={disabled || chosen.length === 0}
        onClick={() => {
          const fields: Partial<BookFields> = {}
          chosen.forEach((key) =>
            Object.assign(fields, { [key]: candidate.fields[key] }),
          )
          onApply(
            fields,
            (candidate.evidence || []).filter((e) =>
              chosen.includes(e.field as keyof BookFields),
            ),
          )
          setChosen([])
        }}
      >
        应用勾选字段到草稿
      </button>
    </article>
  )
}

function PhotoCard({
  photo,
  groups,
  groupId,
  disabled,
  onMove,
  selected,
  onSelect,
}: {
  photo: IntakePhoto
  groups: IntakeGroup[]
  groupId: string
  disabled: boolean
  onMove: (photoId: string, groupId: string) => void
  selected?: boolean
  onSelect?: (selected: boolean) => void
}) {
  const [missingUrl, setMissingUrl] = useState('')
  const type = photo.extraction?.photo_type
  const description =
    (photoLabels[type || 'unknown'] || '照片') + ' · ' + photo.original_name
  return (
    <li className="intake-photo">
      {photo.url && missingUrl !== photo.url ? (
        <a
          href={photo.url}
          target="_blank"
          rel="noreferrer"
          aria-label={'查看原图：' + description}
        >
          <img
            src={photo.url}
            alt={description}
            loading="lazy"
            onError={() => setMissingUrl(photo.url)}
          />
        </a>
      ) : (
        <p className="photo-unavailable">照片已过期或暂不可用</p>
      )}
      <span>{description}</span>
      <small>
        {photo.status === 'failed'
          ? photo.error?.message || '识别失败，可重试'
          : photo.status === 'succeeded'
            ? '已识别'
            : photo.status === 'processing'
              ? '识别中'
              : '待识别'}
      </small>
      {onSelect && (
        <label className="photo-select">
          <input
            type="checkbox"
            checked={selected || false}
            disabled={disabled}
            onChange={(event) => onSelect(event.target.checked)}
          />
          选中拆分
        </label>
      )}
      <label className="book-field">
        <span className="sr-only">{photo.original_name}的归属</span>
        <select
          value={groupId}
          disabled={disabled}
          onChange={(event) => onMove(photo.id, event.target.value)}
        >
          <option value="">未归属照片</option>
          {groups.map((group, index) => (
            <option value={group.id} key={group.id}>
              {group.fields.title || '未命名组 ' + (index + 1)}
            </option>
          ))}
          <option value="new">建立新分组</option>
        </select>
      </label>
    </li>
  )
}

function GroupCard({
  group,
  index,
  intake,
  disabled,
  candidateResult,
  onChange,
  onMove,
  onSplit,
  onMerge,
  onRemove,
}: {
  group: IntakeGroup
  index: number
  intake: Intake
  disabled: boolean
  candidateResult?: CandidateResult
  onChange: (group: IntakeGroup) => void
  onMove: (photoId: string, groupId: string) => void
  onSplit: (groupId: string, photos: string[]) => void
  onMerge: (from: string, to: string) => void
  onRemove: (id: string) => void
}) {
  const [splitPhotos, setSplitPhotos] = useState<string[]>([])
  const [mergeTarget, setMergeTarget] = useState('')
  const otherGroups = intake.groups.filter((item) => item.id !== group.id)
  function changeField<K extends keyof BookFields>(
    key: K,
    value: BookFields[K],
  ) {
    onChange({
      ...group,
      fields: { ...group.fields, [key]: value },
      // Preserve the original evidence; record which fields the user changed.
      evidence: [
        ...(group.evidence || []).filter(
          (e) => e.kind !== 'manual' || e.field !== key,
        ),
        { field: key, value, kind: 'manual', source_label: '人工修改' },
      ],
      existing_book_id: null,
    })
  }
  return (
    <article className="intake-group" aria-labelledby={'group-' + group.id}>
      <div className="intake-group-heading">
        <h3 id={'group-' + group.id}>
          {index + 1}. {group.fields.title || '书名待核对'}
        </h3>
        <label className="photo-select">
          <input
            type="checkbox"
            checked={group.selected !== false}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...group, selected: event.target.checked })
            }
          />
          选中入库
        </label>
      </div>
      {group.existing_book_id !== null && (
        <p className="shared-note">
          已有书目候选 #{group.existing_book_id}
          ，确认时复用；修改资料后需重新核对版本。
        </p>
      )}
      {(group.existing_book_candidates?.length ||
        group.existing_book_id !== null) && (
        <label className="book-field existing-book-choice">
          <span>系统已有版本候选（需明确选择）</span>
          <select
            value={group.existing_book_id ?? ''}
            disabled={disabled}
            onChange={(event) =>
              onChange({
                ...group,
                existing_book_id: event.target.value
                  ? Number(event.target.value)
                  : null,
              })
            }
          >
            <option value="">未选择 · 核对资料后新建</option>
            {group.existing_book_id !== null &&
              !group.existing_book_candidates?.some(
                (candidate) => candidate.id === group.existing_book_id,
              ) && (
                <option value={group.existing_book_id}>
                  已选择书目 #{group.existing_book_id}
                </option>
              )}
            {(group.existing_book_candidates || []).map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.title} · ISBN {candidate.isbn || '未知'} ·{' '}
                {candidate.edition || '版本未知'} ·{' '}
                {candidate.publisher || '出版方未知'}
              </option>
            ))}
          </select>
          <small>
            选择后复用该版本，保留已有书目资料。不同版本请勿合并；修改草稿字段会清空此选择。
          </small>
        </label>
      )}
      <Issues issues={group.issues || []} />
      <ul className="intake-photos">
        {group.photo_ids.map((photoId) => {
          const photo = intake.photos.find((item) => item.id === photoId)
          return (
            photo && (
              <PhotoCard
                key={photo.id}
                photo={photo}
                groups={intake.groups}
                groupId={group.id}
                disabled={disabled}
                onMove={onMove}
                selected={splitPhotos.includes(photo.id)}
                onSelect={(selected) =>
                  setSplitPhotos((previous) =>
                    selected
                      ? [...previous, photo.id]
                      : previous.filter((id) => id !== photo.id),
                  )
                }
              />
            )
          )
        })}
      </ul>
      <div className="intake-group-tools">
        <button
          className="secondary-button"
          type="button"
          disabled={
            disabled || !splitPhotos.some((id) => group.photo_ids.includes(id))
          }
          onClick={() => {
            onSplit(
              group.id,
              splitPhotos.filter((id) => group.photo_ids.includes(id)),
            )
            setSplitPhotos([])
          }}
        >
          将选中照片拆成新组
        </button>
        {otherGroups.length > 0 && (
          <>
            <label className="book-field">
              <span className="sr-only">合并到哪个分组</span>
              <select
                value={mergeTarget}
                disabled={disabled}
                onChange={(event) => setMergeTarget(event.target.value)}
              >
                <option value="">选择合并目标</option>
                {otherGroups.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.fields.title || '书名待核对'}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="secondary-button"
              type="button"
              disabled={
                disabled || !otherGroups.some((item) => item.id === mergeTarget)
              }
              onClick={() => onMerge(group.id, mergeTarget)}
            >
              合并照片，保留目标资料
            </button>
          </>
        )}
        {group.photo_ids.length === 0 && (
          <button
            className="remove-button"
            type="button"
            disabled={disabled}
            onClick={() => onRemove(group.id)}
          >
            移除空分组
          </button>
        )}
      </div>
      <BookFieldsEditor
        fields={group.fields}
        disabled={disabled}
        onField={changeField}
      />
      <details className="extra-fields">
        <summary>字段来源与识别依据</summary>
        <EvidenceList evidence={group.evidence || []} photos={intake.photos} />
      </details>
      {candidateResult && (
        <section className="enrichment-results" aria-label="联网候选">
          <h4>联网候选 · 请确认具体版本</h4>
          <Issues issues={candidateResult.issues || []} />
          {candidateResult.candidates.length === 0 && (
            <p>没有找到可靠候选。可继续手工核对，未知资料留空。</p>
          )}
          {candidateResult.candidates.map((candidate) => (
            <CandidateCard
              key={candidate.id}
              candidate={candidate}
              disabled={disabled}
              onApply={(fields, evidence) =>
                onChange({
                  ...group,
                  fields: { ...group.fields, ...fields },
                  evidence: [...group.evidence, ...evidence],
                  existing_book_id: null,
                })
              }
            />
          ))}
        </section>
      )}
    </article>
  )
}

export default function IntakeReview({
  initial,
  onIntake,
  onConfirmed,
  onAuthRequired,
  onDirty,
}: {
  initial: Intake
  onIntake: (intake: Intake) => void
  onConfirmed: () => Promise<void>
  onAuthRequired: () => void
  onDirty: (dirty: boolean) => void
}) {
  const [intake, setIntake] = useState(initial)
  const [dirty, setDirty] = useState(false)
  const [conflict, setConflict] = useState<Intake | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [candidates, setCandidates] = useState<Record<string, CandidateResult>>(
    {},
  )
  const [addToShelf, setAddToShelf] = useState(false)
  const [confirmation, setConfirmation] = useState<'confirm' | 'cancel' | ''>(
    '',
  )
  const heading = useRef<HTMLHeadingElement>(null)
  const confirmationPanel = useRef<HTMLDivElement>(null)
  const conflictPanel = useRef<HTMLDivElement>(null)
  const actionControl = useRef<HTMLElement | null>(null)
  const apiPath = '/book-intakes/' + intake.id
  const disabled = !!busy || !!conflict || !canEdit(intake)
  const selected = intake.groups.filter((group) => group.selected !== false)
  const running = isRunning(intake)
  const finished = intake.photos.filter(
    (photo) => photo.status === 'succeeded' || photo.status === 'failed',
  ).length
  const unrepresentedPhotos = intake.photos.filter(
    (photo) =>
      !intake.unassigned_photo_ids.includes(photo.id) &&
      !intake.groups.some((group) => group.photo_ids.includes(photo.id)),
  )

  useEffect(() => {
    heading.current?.focus()
  }, [])
  useEffect(() => {
    if (confirmation) confirmationPanel.current?.focus()
  }, [confirmation])
  useEffect(() => {
    if (conflict) conflictPanel.current?.focus()
  }, [conflict])
  useEffect(() => {
    if (busy || !actionControl.current) return
    const control = actionControl.current
    actionControl.current = null
    if (control.isConnected && !control.matches(':disabled')) control.focus()
    else if (!conflict) heading.current?.focus()
  }, [busy, conflict])
  useEffect(() => {
    onDirty(dirty)
    return () => onDirty(false)
  }, [dirty, onDirty])
  useEffect(() => {
    if (!dirty) return
    function preventLeave(event: BeforeUnloadEvent) {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', preventLeave)
    return () => window.removeEventListener('beforeunload', preventLeave)
  }, [dirty])
  useEffect(() => {
    if (!running || busy) return
    const abort = new AbortController()
    let timer: number | undefined
    function poll() {
      request<{ intake: Intake }>(apiPath, { signal: abort.signal })
        .then((result) => {
          if (abort.signal.aborted) return
          setIntake(result.intake)
          onIntake(result.intake)
          setError('')
        })
        .catch((cause: RequestError) => {
          if (abort.signal.aborted) return
          setError('更新进度失败：' + cause.message + '。页面会继续尝试。')
          if (cause.code === 'not_authenticated') onAuthRequired()
        })
        .finally(() => {
          if (!abort.signal.aborted) timer = window.setTimeout(poll, 5000)
        })
    }
    timer = window.setTimeout(poll, 3000)
    return () => {
      abort.abort()
      window.clearTimeout(timer)
    }
  }, [apiPath, running, busy, onIntake, onAuthRequired])

  function adopt(next: Intake) {
    setIntake(next)
    setDirty(false)
    setConflict(null)
    setConfirmation('')
    onIntake(next)
  }
  async function failure(cause: unknown) {
    const issue = cause as RequestError
    setError(issue.message || '请求失败，请稍后重试。')
    if (issue.code === 'not_authenticated') onAuthRequired()
    if (issue.code === 'revision_conflict') {
      setError(
        '草稿已在其他页面或识别任务中更新。本页修改已保留，请核对最新版本后重新编辑。',
      )
      try {
        const result = await request<{ intake: Intake }>(apiPath)
        setConflict(result.intake)
      } catch {
        setError('草稿版本冲突，读取最新版本失败。请点击刷新任务再试。')
      }
    }
  }
  async function action(name: string, work: () => Promise<void>) {
    actionControl.current = document.activeElement as HTMLElement | null
    setBusy(name)
    setError('')
    setNotice('')
    try {
      await work()
    } catch (cause) {
      await failure(cause)
    } finally {
      setBusy('')
    }
  }
  function edit(
    groups: IntakeGroup[],
    unassigned = intake.unassigned_photo_ids,
  ) {
    setIntake({ ...intake, groups, unassigned_photo_ids: unassigned })
    setDirty(true)
    setConfirmation('')
    setNotice('')
  }
  function newGroup(photos: string[]): IntakeGroup {
    return {
      id: 'manual-' + crypto.randomUUID(),
      photo_ids: photos,
      fields: emptyFields(),
      evidence: [],
      issues: ['人工分组，请按照片填写并核对具体版本。'],
      existing_book_id: null,
      selected: true,
    }
  }
  function movePhoto(photoId: string, target: string) {
    const next = intake.groups.map((group) => ({
      ...group,
      photo_ids: group.photo_ids.filter((id) => id !== photoId),
    }))
    if (target === 'new') next.push(newGroup([photoId]))
    else if (target) {
      const destination = next.find((group) => group.id === target)
      destination?.photo_ids.push(photoId)
    }
    edit(
      next,
      target
        ? intake.unassigned_photo_ids.filter((id) => id !== photoId)
        : [...new Set([...intake.unassigned_photo_ids, photoId])],
    )
    setCandidates({})
  }
  function splitGroup(groupId: string, photos: string[]) {
    edit([
      ...intake.groups.map((group) =>
        group.id === groupId
          ? {
              ...group,
              photo_ids: group.photo_ids.filter((id) => !photos.includes(id)),
            }
          : group,
      ),
      newGroup(photos),
    ])
    setCandidates({})
  }
  function mergeGroup(from: string, to: string) {
    const source = intake.groups.find((group) => group.id === from)
    if (!source) return
    edit(
      intake.groups
        .filter((group) => group.id !== from)
        .map((group) =>
          group.id === to
            ? {
                ...group,
                photo_ids: [...group.photo_ids, ...source.photo_ids],
                evidence: [...group.evidence, ...source.evidence],
                issues: [
                  ...group.issues,
                  ...source.issues,
                  '已合并照片并保留目标组资料，请核对是否同一本、同一版本。',
                ],
                existing_book_id: null,
              }
            : group,
        ),
    )
    setCandidates({})
  }
  async function save() {
    const result = await jsonRequest<{ intake: Intake }>(
      apiPath + '/groups',
      'PATCH',
      {
        revision: intake.revision,
        groups: intake.groups.map((group) => ({
          ...group,
          fields: cleanFields(group.fields),
        })),
        unassigned_photo_ids: intake.unassigned_photo_ids,
      },
    )
    adopt(result.intake)
    setNotice('分组和资料已保存。核对选中组后，可统一确认入库。')
  }
  return (
    <section
      className="intake-review"
      aria-labelledby="intake-review-heading"
      aria-busy={!!busy}
    >
      <div className="intake-group-heading">
        <h3 id="intake-review-heading" ref={heading} tabIndex={-1}>
          照片录入 · {intakeLabels[intake.status]}
        </h3>
        <button
          className="text-button"
          type="button"
          disabled={!!busy}
          onClick={() =>
            void action('refresh', async () => {
              const result = await request<{ intake: Intake }>(apiPath)
              if (dirty) {
                setConflict(result.intake)
                setNotice('本页有未保存修改。载入服务器草稿会放弃这些修改。')
              } else adopt(result.intake)
            })
          }
        >
          刷新任务
        </button>
      </div>
      <p className="book-meta">
        {intake.mode === 'batch' ? '省钱 Batch' : '实时识别'} ·{' '}
        {new Date(intake.created_at).toLocaleString('zh-CN')} · 已处理{' '}
        {finished}/{intake.photos.length} 张
      </p>
      {running && (
        <>
          <progress
            max={Math.max(1, intake.photos.length)}
            value={finished}
            aria-label="照片识别进度"
          />
          <p className="question-hint">
            任务由后台处理，页面会自动更新。Batch
            完成时间取决于调度；离开或刷新后可从任务列表继续。
          </p>
        </>
      )}
      <UsageSummary intake={intake} />
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
      {conflict && (
        <div
          className="conflict-panel"
          role="alert"
          ref={conflictPanel}
          tabIndex={-1}
        >
          <p>
            服务器草稿版本 {conflict.revision}，状态：
            {intakeLabels[conflict.status]}。
            {conflict.groups
              .map((group) => group.fields.title || '未命名组')
              .join('、')}
          </p>
          <p>本页修改尚未写入服务器。载入最新版本后，需要重新核对修改。</p>
          <button
            className="secondary-button"
            type="button"
            disabled={!!busy}
            onClick={() => {
              adopt(conflict)
              setCandidates({})
              setError('')
              setNotice('已载入最新草稿。')
            }}
          >
            放弃本页修改，载入最新草稿
          </button>
        </div>
      )}
      <Issues issues={intake.errors || []} />
      {intake.status === 'confirmed' && (
        <div className="success-banner" role="status">
          <p>所选分组已统一入库，此任务已结束。</p>
          <ul>
            {(intake.confirmed_books || []).map((book) => (
              <li key={book.group_id}>
                书目 #{book.book_id} · {book.created ? '新建' : '复用已有书目'}
              </li>
            ))}
          </ul>
        </div>
      )}
      {intake.status === 'cancelled' && (
        <p className="question-hint">
          任务已取消，不会自动入库。取消前已完成的识别可能仍产生费用。
        </p>
      )}
      {intake.groups.length === 0 &&
        !running &&
        intake.status !== 'confirmed' && (
          <p className="question-hint">
            尚无可确认的图书分组。识别失败可重试，也可通过手工新增录入资料。
          </p>
        )}
      <div className="intake-groups">
        {intake.groups.map((group, index) => (
          <GroupCard
            group={group}
            index={index}
            intake={intake}
            disabled={disabled}
            candidateResult={
              candidates[group.id] ||
              (group.enrichment_candidates
                ? { candidates: group.enrichment_candidates, issues: [] }
                : undefined)
            }
            key={group.id}
            onChange={(next) =>
              edit(
                intake.groups.map((item) =>
                  item.id === group.id ? next : item,
                ),
              )
            }
            onMove={movePhoto}
            onSplit={splitGroup}
            onMerge={mergeGroup}
            onRemove={(id) =>
              edit(intake.groups.filter((group) => group.id !== id))
            }
          />
        ))}
      </div>
      {intake.unassigned_photo_ids.length > 0 && (
        <section className="unassigned-photos">
          <h4>未归属照片 · {intake.unassigned_photo_ids.length} 张</h4>
          <p>请核对后移动到对应组，或建立新组；不确定的照片可以保留。</p>
          <ul className="intake-photos">
            {intake.unassigned_photo_ids.map((id) => {
              const photo = intake.photos.find((item) => item.id === id)
              return (
                photo && (
                  <PhotoCard
                    key={id}
                    photo={photo}
                    groups={intake.groups}
                    groupId=""
                    disabled={disabled}
                    onMove={movePhoto}
                  />
                )
              )
            })}
          </ul>
        </section>
      )}
      {unrepresentedPhotos.length > 0 && (
        <section className="unassigned-photos">
          <h4>待处理或识别失败的照片</h4>
          <ul className="intake-photos">
            {unrepresentedPhotos.map((photo) => (
              <PhotoCard
                key={photo.id}
                photo={photo}
                groups={intake.groups}
                groupId=""
                disabled={disabled}
                onMove={movePhoto}
              />
            ))}
          </ul>
        </section>
      )}
      {canEdit(intake) && (
        <div className="intake-action-panel">
          <p role="status">
            {dirty
              ? '有未保存修改，请先保存草稿。'
              : '草稿已保存。请核对每组的书名、ISBN、版本及字段来源。'}
            已选 {selected.length}/{intake.groups.length} 组。
          </p>
          <div className="management-actions">
            <button
              className="secondary-button"
              type="button"
              disabled={disabled || !dirty}
              onClick={() => void action('save', save)}
            >
              {busy === 'save' ? '正在保存…' : '保存分组和资料'}
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={disabled || dirty || selected.length === 0}
              onClick={() =>
                void action('enrichment', async () => {
                  const result = await jsonRequest<{
                    intake: Intake
                    candidates: Record<string, CandidateResult>
                  }>(apiPath + '/enrichment', 'POST', {
                    revision: intake.revision,
                    group_ids: selected.map((group) => group.id),
                  })
                  adopt(result.intake)
                  setCandidates(result.candidates)
                  setNotice(
                    '候选已展示在各分组下。勾选字段并应用后，请保存草稿；联网查询不会自动覆盖资料。',
                  )
                })
              }
            >
              {busy === 'enrichment'
                ? '正在查找候选…'
                : '为选中组联网补全（可选）'}
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={disabled || dirty || selected.length === 0}
              onClick={() => setConfirmation('confirm')}
            >
              核对并统一确认入库
            </button>
          </div>
        </div>
      )}
      {(intake.status === 'partial_failure' || intake.status === 'failed') && (
        <div className="management-actions">
          <button
            className="secondary-button"
            type="button"
            disabled={!!busy || dirty || !!conflict}
            onClick={() =>
              void action('retry', async () => {
                const result = await jsonRequest<{ intake: Intake }>(
                  apiPath + '/retry',
                  'POST',
                  { revision: intake.revision },
                )
                adopt(result.intake)
                setCandidates({})
                setNotice('已请求重试失败项，成功照片的提取结果会保留。')
              })
            }
          >
            {busy === 'retry' ? '正在提交…' : '仅重试失败项'}
          </button>
          {dirty && <p>重试前请保存当前分组和资料。</p>}
        </div>
      )}
      {(running || canEdit(intake) || intake.status === 'failed') && (
        <button
          className="remove-button"
          type="button"
          disabled={!!busy || !!conflict || dirty}
          onClick={() => setConfirmation('cancel')}
        >
          取消此任务
        </button>
      )}
      {confirmation && (
        <div
          className="confirmation-panel"
          role="group"
          aria-label={confirmation === 'confirm' ? '确认入库' : '确认取消'}
          ref={confirmationPanel}
          tabIndex={-1}
        >
          {confirmation === 'confirm' ? (
            <>
              <h4>确认以下 {selected.length} 组资料入库</h4>
              <ul>
                {selected.map((group) => (
                  <li key={group.id}>
                    {group.fields.title || '书名未填写'} · ISBN{' '}
                    {group.fields.isbn || '未知'} ·{' '}
                    {group.fields.edition || '版本未填写'}
                  </li>
                ))}
              </ul>
              <p>
                排除的分组和未归属照片不会入库。确认后，此任务结束且不能再修改。
              </p>
              <label className="inline-choice">
                <input
                  type="checkbox"
                  checked={addToShelf}
                  disabled={!!busy}
                  onChange={(event) => setAddToShelf(event.target.checked)}
                />
                同时加入我的书架
              </label>
            </>
          ) : (
            <>
              <h4>确认取消此识别任务？</h4>
              <p>取消后不会自动入库。已完成的供应商请求可能仍产生费用。</p>
            </>
          )}
          <div className="management-actions">
            <button
              className="primary-button"
              type="button"
              disabled={
                !!busy ||
                dirty ||
                !!conflict ||
                (confirmation === 'confirm' &&
                  selected.some((group) => !group.fields.title?.trim()))
              }
              onClick={() =>
                void action(confirmation, async () => {
                  if (confirmation === 'confirm') {
                    const result = await jsonRequest<{ intake: Intake }>(
                      apiPath + '/confirm',
                      'POST',
                      {
                        revision: intake.revision,
                        group_ids: selected.map((group) => group.id),
                        add_to_shelf: addToShelf,
                      },
                    )
                    adopt(result.intake)
                    setCandidates({})
                    await onConfirmed()
                    setNotice('所选图书已确认入库。')
                  } else {
                    const result = await jsonRequest<{ intake: Intake }>(
                      apiPath + '/cancel',
                      'POST',
                      { revision: intake.revision },
                    )
                    adopt(result.intake)
                  }
                })
              }
            >
              {busy
                ? '正在提交…'
                : confirmation === 'confirm'
                  ? '确认入库'
                  : '确认取消任务'}
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={!!busy}
              onClick={() => setConfirmation('')}
            >
              返回核对
            </button>
          </div>
          {confirmation === 'confirm' &&
            selected.some((group) => !group.fields.title?.trim()) && (
              <p className="field-error">
                选中组仍有空书名，请返回填写并保存。
              </p>
            )}
        </div>
      )}
    </section>
  )
}
