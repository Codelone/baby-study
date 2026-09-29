import { useEffect, useRef, useState } from 'react'
import { jsonRequest, request, RequestError } from './api'

type Option = { value: string; label: string }
type Question = {
  id: string
  title: string
  type:
    | 'single'
    | 'multi'
    | 'birth_month'
    | 'languages'
    | 'goals'
    | 'schedule'
    | 'graded'
  hint?: string
  options?: Option[]
  exclusive?: string[]
  frequency_options?: Option[]
  windows?: Option[]
  usage_options?: Option[]
}
type Assessment = {
  id: number
  status: 'draft' | 'completed'
  started_at: string
  completed_at: string | null
  answers: Record<string, unknown>
  visible_question_ids: string[]
  state_id: number | null
}
type Evidence = { question_id: string; question: string; answer: string }
type ProfileSection = {
  id: string
  title: string
  summary: string
  certainty: string
  evidence: Evidence[]
}
type State = {
  id: number
  assessment_id: number
  version: number
  created_at: string
  confirmed_at: string | null
  profile: {
    branch: string
    branch_label: string
    sections: ProfileSection[]
    starting_point: {
      summary: string
      suggested_minutes: number
      evidence: Evidence[]
    }
    evidence_gaps: string[]
    conflicts: { text: string; question_ids: string[] }[]
    reported_levels: { system: string; native_level: string; usage: string }[]
  }
}

const VERSION = '2026-09-v1'

function dateLabel(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  }).format(new Date(value))
}

function initialAnswer(question: Question, value: unknown): unknown {
  if (value !== undefined) return value
  switch (question.type) {
    case 'single':
    case 'birth_month':
      return ''
    case 'multi':
      return []
    case 'languages':
      return { languages: [], english_frequency: '' }
    case 'goals':
      return { primary: '', secondary: [] }
    case 'schedule':
      return {
        available_days: 0,
        max_session_minutes: 0,
        preferred_windows: [],
        weekend_differs: false,
      }
    case 'graded':
      return { status: '', systems: [], primary_system: null }
  }
}

function readyToSave(question: Question, value: unknown): boolean {
  if (question.type === 'single')
    return typeof value === 'string' && value.length > 0
  if (question.type === 'birth_month')
    return typeof value === 'string' && /^\d{4}-\d{2}$/.test(value)
  if (question.type === 'multi') return Array.isArray(value) && value.length > 0
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  if (question.type === 'languages')
    return (
      Array.isArray(item.languages) &&
      item.languages.length > 0 &&
      !!item.english_frequency
    )
  if (question.type === 'goals') return !!item.primary
  if (question.type === 'schedule')
    return (
      Number(item.available_days) >= 1 && Number(item.max_session_minutes) >= 3
    )
  if (question.type === 'graded')
    return (
      item.status === 'none' ||
      item.status === 'unknown' ||
      (item.status === 'using' &&
        Array.isArray(item.systems) &&
        item.systems.length > 0)
    )
  return false
}

function toggleChoice(
  current: string[],
  option: string,
  exclusive: string[] = [],
): string[] {
  if (current.includes(option)) return current.filter((item) => item !== option)
  if (exclusive.includes(option)) return [option]
  return [...current.filter((item) => !exclusive.includes(item)), option]
}

function QuestionInput({
  question,
  value,
  onChange,
}: {
  question: Question
  value: unknown
  onChange: (value: unknown) => void
}) {
  if (question.type === 'birth_month') {
    return (
      <label className="question-field">
        <span>出生年月</span>
        <input
          type="month"
          value={value as string}
          onChange={(event) => onChange(event.target.value)}
          required
        />
      </label>
    )
  }
  if (question.type === 'single' || question.type === 'multi') {
    const selected = question.type === 'multi' ? (value as string[]) : []
    return (
      <fieldset className="choice-group">
        <legend className="sr-only">{question.title}</legend>
        {question.options?.map((option) => (
          <label className="choice" key={option.value}>
            <input
              type={question.type === 'single' ? 'radio' : 'checkbox'}
              name={question.id}
              checked={
                question.type === 'single'
                  ? value === option.value
                  : selected.includes(option.value)
              }
              onChange={() =>
                onChange(
                  question.type === 'single'
                    ? option.value
                    : toggleChoice(selected, option.value, question.exclusive),
                )
              }
            />
            <span>{option.label}</span>
          </label>
        ))}
      </fieldset>
    )
  }
  if (question.type === 'languages') {
    const answer = value as { languages: string[]; english_frequency: string }
    return (
      <>
        <fieldset className="choice-group">
          <legend>日常接触的语言，可多选</legend>
          {question.options?.map((option) => (
            <label className="choice" key={option.value}>
              <input
                type="checkbox"
                checked={answer.languages.includes(option.value)}
                onChange={() =>
                  onChange({
                    ...answer,
                    languages: toggleChoice(
                      answer.languages,
                      option.value,
                      question.exclusive,
                    ),
                  })
                }
              />
              <span>{option.label}</span>
            </label>
          ))}
        </fieldset>
        <label className="question-field">
          <span>英语接触频率</span>
          <select
            value={answer.english_frequency}
            onChange={(event) =>
              onChange({ ...answer, english_frequency: event.target.value })
            }
          >
            <option value="">请选择</option>
            {question.frequency_options?.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </>
    )
  }
  if (question.type === 'goals') {
    const answer = value as { primary: string; secondary: string[] }
    return (
      <>
        <label className="question-field">
          <span>首要目标</span>
          <select
            value={answer.primary}
            onChange={(event) =>
              onChange({
                primary: event.target.value,
                secondary: answer.secondary.filter(
                  (item) =>
                    item !== event.target.value &&
                    event.target.value !== 'unknown',
                ),
              })
            }
          >
            <option value="">请选择</option>
            {question.options?.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        {answer.primary && answer.primary !== 'unknown' && (
          <fieldset className="choice-group">
            <legend>次要目标，可选至多两个</legend>
            {question.options
              ?.filter(
                (option) =>
                  option.value !== answer.primary && option.value !== 'unknown',
              )
              .map((option) => (
                <label className="choice" key={option.value}>
                  <input
                    type="checkbox"
                    checked={answer.secondary.includes(option.value)}
                    disabled={
                      answer.secondary.length >= 2 &&
                      !answer.secondary.includes(option.value)
                    }
                    onChange={() =>
                      onChange({
                        ...answer,
                        secondary: toggleChoice(answer.secondary, option.value),
                      })
                    }
                  />
                  <span>{option.label}</span>
                </label>
              ))}
          </fieldset>
        )}
      </>
    )
  }
  if (question.type === 'schedule') {
    const answer = value as {
      available_days: number
      max_session_minutes: number
      preferred_windows: string[]
      weekend_differs: boolean
    }
    return (
      <>
        <div className="question-pair">
          <label className="question-field">
            <span>每周可安排天数</span>
            <select
              value={answer.available_days || ''}
              onChange={(event) =>
                onChange({
                  ...answer,
                  available_days: Number(event.target.value),
                })
              }
            >
              <option value="">请选择</option>
              {[1, 2, 3, 4, 5, 6, 7].map((day) => (
                <option key={day} value={day}>
                  {day} 天
                </option>
              ))}
            </select>
          </label>
          <label className="question-field">
            <span>单次可接受时长（3～60 分钟）</span>
            <input
              type="number"
              min="3"
              max="60"
              list="duration-suggestions"
              value={answer.max_session_minutes || ''}
              onChange={(event) =>
                onChange({
                  ...answer,
                  max_session_minutes: Number(event.target.value),
                })
              }
            />
            <datalist id="duration-suggestions">
              {[3, 5, 10, 15, 20].map((minutes) => (
                <option key={minutes} value={minutes} />
              ))}
            </datalist>
          </label>
        </div>
        <fieldset className="choice-group">
          <legend>常见时间段，可多选</legend>
          {question.windows?.map((option) => (
            <label className="choice" key={option.value}>
              <input
                type="checkbox"
                checked={answer.preferred_windows.includes(option.value)}
                onChange={() =>
                  onChange({
                    ...answer,
                    preferred_windows: toggleChoice(
                      answer.preferred_windows,
                      option.value,
                      ['variable'],
                    ),
                  })
                }
              />
              <span>{option.label}</span>
            </label>
          ))}
        </fieldset>
        <label className="inline-choice">
          <input
            type="checkbox"
            checked={answer.weekend_differs}
            onChange={(event) =>
              onChange({ ...answer, weekend_differs: event.target.checked })
            }
          />
          <span>工作日与周末的安排不同</span>
        </label>
      </>
    )
  }
  const answer = value as {
    status: string
    systems: { system: string; native_level: string; usage: string }[]
    primary_system: string | null
  }
  return (
    <>
      <fieldset className="choice-group">
        <legend>使用情况</legend>
        {[
          ['none', '尚未开始'],
          ['using', '正在使用'],
          ['unknown', '不确定'],
        ].map(([status, label]) => (
          <label className="choice" key={status}>
            <input
              type="radio"
              name="graded-status"
              checked={answer.status === status}
              onChange={() =>
                onChange({
                  status,
                  systems: status === 'using' ? answer.systems : [],
                  primary_system:
                    status === 'using' ? answer.primary_system : null,
                })
              }
            />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>
      {answer.status === 'using' && (
        <>
          <fieldset className="choice-group">
            <legend>正在使用的体系，可多选</legend>
            {question.options?.map((option) => {
              const system = answer.systems.find(
                (item) => item.system === option.value,
              )
              return (
                <div className="graded-system" key={option.value}>
                  <label className="choice">
                    <input
                      type="checkbox"
                      checked={!!system}
                      onChange={() => {
                        const systems = system
                          ? answer.systems.filter(
                              (item) => item.system !== option.value,
                            )
                          : [
                              ...answer.systems,
                              {
                                system: option.value,
                                native_level: '',
                                usage: 'unknown',
                              },
                            ]
                        onChange({
                          ...answer,
                          systems,
                          primary_system:
                            answer.primary_system === option.value
                              ? null
                              : answer.primary_system,
                        })
                      }}
                    />
                    <span>{option.label}</span>
                  </label>
                  {system && (
                    <div className="graded-details">
                      <label className="question-field">
                        <span>当前原生级别（可留空）</span>
                        <input
                          type="text"
                          maxLength={80}
                          value={system.native_level}
                          onChange={(event) =>
                            onChange({
                              ...answer,
                              systems: answer.systems.map((item) =>
                                item.system === option.value
                                  ? {
                                      ...item,
                                      native_level: event.target.value,
                                    }
                                  : item,
                              ),
                            })
                          }
                        />
                      </label>
                      <label className="question-field">
                        <span>主要使用方式</span>
                        <select
                          value={system.usage}
                          onChange={(event) =>
                            onChange({
                              ...answer,
                              systems: answer.systems.map((item) =>
                                item.system === option.value
                                  ? { ...item, usage: event.target.value }
                                  : item,
                              ),
                            })
                          }
                        >
                          {question.usage_options?.map((usage) => (
                            <option key={usage.value} value={usage.value}>
                              {usage.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  )}
                </div>
              )
            })}
          </fieldset>
          {answer.systems.length > 0 && (
            <label className="question-field">
              <span>希望延续的主体系（可不选）</span>
              <select
                value={answer.primary_system || ''}
                onChange={(event) =>
                  onChange({
                    ...answer,
                    primary_system: event.target.value || null,
                  })
                }
              >
                <option value="">暂不指定</option>
                {answer.systems.map((system) => (
                  <option key={system.system} value={system.system}>
                    {
                      question.options?.find(
                        (item) => item.value === system.system,
                      )?.label
                    }
                  </option>
                ))}
              </select>
            </label>
          )}
        </>
      )}
    </>
  )
}

function QuestionStep({
  assessment,
  question,
  index,
  total,
  onSaved,
  onNext,
  onBack,
}: {
  assessment: Assessment
  question: Question
  index: number
  total: number
  onSaved: (assessment: Assessment) => void
  onNext: (assessment: Assessment, questionId: string) => Promise<void>
  onBack: () => void
}) {
  const initial = assessment.answers[question.id]
  const [value, setValue] = useState<unknown>(() =>
    initialAnswer(question, initial),
  )
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const queue = useRef<Promise<Assessment>>(Promise.resolve(assessment))
  const lastSaved = useRef(JSON.stringify(initial))
  const lastQueued = useRef(JSON.stringify(initial))
  const latestValue = useRef(value)
  latestValue.current = value

  async function save(current: unknown): Promise<Assessment> {
    const encoded = JSON.stringify(current)
    if (encoded === lastQueued.current) return queue.current
    lastQueued.current = encoded
    const pending = queue.current
      .catch(() => assessment)
      .then(async () => {
        setStatus('正在自动保存…')
        const result = await jsonRequest<{ assessment: Assessment }>(
          `/assessments/${assessment.id}/responses/${question.id}`,
          'PUT',
          { answer_value: current },
        )
        lastSaved.current = encoded
        onSaved(result.assessment)
        setStatus('已自动保存')
        setError('')
        return result.assessment
      })
      .catch((cause: unknown) => {
        if (lastQueued.current === encoded)
          lastQueued.current = lastSaved.current
        throw cause
      })
    queue.current = pending
    return pending
  }

  useEffect(() => {
    if (
      !readyToSave(question, value) ||
      JSON.stringify(value) === lastQueued.current
    )
      return
    timer.current = setTimeout(() => {
      void save(value).catch((cause: RequestError) => {
        setStatus('')
        setError(cause.message)
      })
    }, 700)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [value])

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current)
      const pending = latestValue.current
      if (
        readyToSave(question, pending) &&
        JSON.stringify(pending) !== lastQueued.current
      ) {
        void save(pending).catch(() => undefined)
      }
    }
  }, [])

  async function advance() {
    if (!readyToSave(question, value)) {
      setError('请先完成这道题，再继续。')
      return
    }
    if (timer.current) clearTimeout(timer.current)
    setBusy(true)
    try {
      const saved = await save(value)
      await onNext(saved, question.id)
    } catch (cause) {
      setError((cause as RequestError).message)
      setBusy(false)
    }
  }

  async function goBack() {
    if (timer.current) clearTimeout(timer.current)
    if (readyToSave(question, value)) {
      try {
        await save(value)
      } catch (cause) {
        setError((cause as RequestError).message)
        return
      }
    }
    onBack()
  }

  return (
    <div className="assessment-card">
      <div className="step-header">
        <p className="eyebrow">初始评估 · {question.id}</p>
        <span>
          第 {index + 1} / {total} 题
        </span>
      </div>
      <progress value={index + 1} max={total} aria-label="评估进度" />
      <h2>{question.title}</h2>
      {question.hint && <p className="question-hint">{question.hint}</p>}
      <QuestionInput question={question} value={value} onChange={setValue} />
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      <div className="step-footer">
        <button
          className="text-button"
          type="button"
          disabled={index === 0 || busy}
          onClick={() => void goBack()}
        >
          上一题
        </button>
        <span className="save-status" aria-live="polite">
          {status || '可随时离开，已答题会保存'}
        </span>
        <button
          className="primary-button"
          type="button"
          disabled={busy}
          onClick={() => void advance()}
        >
          {index + 1 === total ? '生成画像' : '下一题'}
        </button>
      </div>
    </div>
  )
}

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return null
  return (
    <details className="evidence-details">
      <summary>查看判断依据（{evidence.length}）</summary>
      <ul>
        {evidence.map((item) => (
          <li key={item.question_id}>
            <strong>
              {item.question_id} · {item.question}
            </strong>
            <span>{item.answer}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}

function ProfileView({
  state,
  current,
  onConfirm,
  onCorrect,
  onCurrent,
}: {
  state: State
  current: boolean
  onConfirm: () => void
  onCorrect: () => void
  onCurrent: () => void
}) {
  return (
    <div className="profile-view">
      <div className="section-heading">
        <div>
          <p className="eyebrow">LEARNING PORTRAIT · 第 {state.version} 版</p>
          <h2>初步状态画像</h2>
        </div>
        <span className="profile-date">{dateLabel(state.created_at)}</span>
      </div>
      {!current && (
        <button className="text-button" type="button" onClick={onCurrent}>
          ← 返回当前画像
        </button>
      )}
      <div className="profile-intro">
        <p className="eyebrow">当前互动起点 · {state.profile.branch_label}</p>
        <p>{state.profile.starting_point.summary}</p>
        <EvidenceList evidence={state.profile.starting_point.evidence} />
      </div>
      <p className="profile-disclaimer">
        这是一份可修正的共读起点。没有观察到的表现会保持未知，实际共读可以继续校准。
      </p>
      <div className="profile-grid">
        {state.profile.sections.map((section) => (
          <article className="profile-section" key={section.id}>
            <div className="profile-section-title">
              <h3>{section.title}</h3>
              <span>{section.certainty}</span>
            </div>
            <p>{section.summary}</p>
            <EvidenceList evidence={section.evidence} />
          </article>
        ))}
      </div>
      {(state.profile.evidence_gaps.length > 0 ||
        state.profile.conflicts.length > 0) && (
        <section className="profile-gaps">
          <h3>接下来留意什么</h3>
          <ul>
            {state.profile.evidence_gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
          {state.profile.conflicts.map((conflict) => (
            <p className="profile-conflict" key={conflict.text}>
              需要核对 {conflict.question_ids.join('、')}：{conflict.text}
            </p>
          ))}
        </section>
      )}
      {current && (
        <div className="profile-actions">
          {!state.confirmed_at ? (
            <button
              className="primary-button"
              type="button"
              onClick={onConfirm}
            >
              确认这份画像
            </button>
          ) : (
            <span className="confirmed-label">家长已确认</span>
          )}
          <button className="text-button" type="button" onClick={onCorrect}>
            修改答案并重新生成
          </button>
        </div>
      )}
    </div>
  )
}

export default function AssessmentArea({
  shelfCount,
  onGoShelf,
  onAuthRequired,
}: {
  shelfCount: number
  onGoShelf: () => void
  onAuthRequired: () => void
}) {
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [questions, setQuestions] = useState<Question[]>([])
  const [index, setIndex] = useState(0)
  const [currentState, setCurrentState] = useState<State | null>(null)
  const [viewedState, setViewedState] = useState<State | null>(null)
  const [history, setHistory] = useState<Assessment[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function handleError(cause: unknown) {
    const failure = cause as RequestError
    if (failure.code === 'not_authenticated') onAuthRequired()
    setError(failure.message)
  }

  async function fetchQuestions(session: Assessment) {
    const result = await request<{ questions: Question[] }>(
      `/assessment/questions?session_id=${session.id}`,
    )
    setQuestions(result.questions)
    return result.questions
  }

  useEffect(() => {
    let active = true
    Promise.all([
      request<{ assessment: Assessment | null }>('/assessments/draft'),
      request<{ state: State | null }>('/state/current'),
      request<{ assessments: Assessment[] }>('/assessments'),
    ])
      .then(async ([draft, state, sessions]) => {
        if (!active) return
        setAssessment(draft.assessment)
        setCurrentState(state.state)
        setViewedState(state.state)
        setHistory(
          sessions.assessments.filter((item) => item.status === 'completed'),
        )
        if (draft.assessment) {
          const result = await request<{ questions: Question[] }>(
            `/assessment/questions?session_id=${draft.assessment.id}`,
          )
          if (!active) return
          setQuestions(result.questions)
          const firstMissing = result.questions.findIndex(
            (question) => !(question.id in draft.assessment!.answers),
          )
          setIndex(
            firstMissing >= 0
              ? firstMissing
              : Math.max(0, result.questions.length - 1),
          )
        }
      })
      .catch((cause: unknown) => {
        if (active) handleError(cause)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  async function start(copyFromId?: number) {
    setBusy(true)
    setError('')
    try {
      const result = await jsonRequest<{ assessment: Assessment }>(
        '/assessments',
        'POST',
        {
          questionnaire_version: VERSION,
          ...(copyFromId ? { copy_from_id: copyFromId } : {}),
        },
      )
      setAssessment(result.assessment)
      await fetchQuestions(result.assessment)
      setIndex(0)
    } catch (cause) {
      handleError(cause)
    } finally {
      setBusy(false)
    }
  }

  async function next(saved: Assessment, questionId: string) {
    setAssessment(saved)
    const refreshed = await fetchQuestions(saved)
    const position = refreshed.findIndex(
      (question) => question.id === questionId,
    )
    if (position >= 0 && position + 1 < refreshed.length) {
      setIndex(position + 1)
      return
    }
    const result = await jsonRequest<{ state: State }>(
      `/assessments/${saved.id}/complete`,
      'POST',
      {},
    )
    setAssessment(null)
    setCurrentState(result.state)
    setViewedState(result.state)
    const sessions = await request<{ assessments: Assessment[] }>(
      '/assessments',
    )
    setHistory(
      sessions.assessments.filter((item) => item.status === 'completed'),
    )
  }

  async function confirm() {
    if (!currentState) return
    setBusy(true)
    setError('')
    try {
      const result = await jsonRequest<{ state: State }>(
        `/states/${currentState.id}/confirm`,
        'POST',
        {},
      )
      setCurrentState(result.state)
      setViewedState(result.state)
    } catch (cause) {
      handleError(cause)
    } finally {
      setBusy(false)
    }
  }

  async function showHistory(stateId: number) {
    setBusy(true)
    setError('')
    try {
      const result = await request<{ state: State }>(`/states/${stateId}`)
      setViewedState(result.state)
    } catch (cause) {
      handleError(cause)
    } finally {
      setBusy(false)
    }
  }

  const question = questions[index]
  return (
    <section aria-label="初始评估与状态画像" className="assessment-area">
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p className="empty-state">正在读取评估记录…</p>
      ) : assessment && question ? (
        <>
          <div className="assessment-topline">
            <span>填写中的评估会自动保存；完成后会生成一份新的画像版本。</span>
            <button className="text-button" type="button" onClick={onGoShelf}>
              查看家庭书架 →
            </button>
          </div>
          <QuestionStep
            key={`${assessment.id}-${question.id}`}
            assessment={assessment}
            question={question}
            index={index}
            total={questions.length}
            onSaved={setAssessment}
            onNext={next}
            onBack={() => setIndex((value) => Math.max(0, value - 1))}
          />
        </>
      ) : viewedState ? (
        <ProfileView
          state={viewedState}
          current={viewedState.id === currentState?.id}
          onConfirm={() => void confirm()}
          onCorrect={() => void start(currentState?.assessment_id)}
          onCurrent={() => setViewedState(currentState)}
        />
      ) : (
        <div className="assessment-welcome">
          <p className="eyebrow">INITIAL ASSESSMENT</p>
          <h2>从孩子最近的真实表现开始</h2>
          <p>
            回答共读习惯、互动方式、兴趣和家庭安排。孩子不用答题；不确定的地方可以如实选择“没观察过”。
          </p>
          <button
            className="primary-button"
            type="button"
            disabled={busy}
            onClick={() => void start()}
          >
            开始初始评估
          </button>
        </div>
      )}
      {!loading && (
        <>
          <div className="shelf-reminder">
            <span>
              家庭书架已确认 {shelfCount} 本单本读物。
              {shelfCount === 0
                ? '可以先完成评估，再补充书架。'
                : '画像会结合书架为下一阶段提供起点。'}
            </span>
            <button className="text-button" type="button" onClick={onGoShelf}>
              管理书架 →
            </button>
          </div>
          {history.length > 0 && !assessment && (
            <section className="assessment-history">
              <h3>历史画像</h3>
              <ul>
                {history.map((item) => (
                  <li key={item.id}>
                    <span>
                      {item.completed_at
                        ? dateLabel(item.completed_at)
                        : '评估记录'}{' '}
                      · 评估 #{item.id}
                    </span>
                    {item.state_id && (
                      <button
                        type="button"
                        className="text-button"
                        disabled={busy}
                        onClick={() => void showHistory(item.state_id!)}
                      >
                        查看画像
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </section>
  )
}
