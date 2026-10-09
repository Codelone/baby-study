type ApiError = {
  error?: { code?: string; message?: string; details?: unknown }
}

function detailMessage(details: unknown): string | null {
  if (typeof details === 'string') return details
  if (Array.isArray(details)) {
    for (const item of details) {
      const message = detailMessage(item)
      if (message) return message
    }
  }
  if (details && typeof details === 'object') {
    for (const item of Object.values(details)) {
      const message = detailMessage(item)
      if (message) return message
    }
  }
  return null
}

export class RequestError extends Error {
  code?: string
  details?: unknown
  status?: number
  requestId?: string | null

  constructor(message: string, code?: string, details?: unknown) {
    super(message)
    this.code = code
    this.details = details
  }
}

export function csrfToken(): string {
  const token = document.cookie
    .split('; ')
    .find((entry) => entry.startsWith('csrftoken='))
    ?.split('=')[1]
  return token ? decodeURIComponent(token) : ''
}

type RequestOptions = RequestInit & { timeoutMs?: number }

export async function request<T>(
  path: string,
  init?: RequestOptions,
): Promise<T> {
  const { timeoutMs = 30000, signal, ...options } = init ?? {}
  const controller = new AbortController()
  let timedOut = false
  const cancel = () => controller.abort(signal?.reason)
  signal?.addEventListener('abort', cancel, { once: true })
  if (signal?.aborted) cancel()
  const timer = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  try {
    const response = await fetch('/baby/study' + path, {
      credentials: 'same-origin',
      ...options,
      signal: controller.signal,
    })
    if (response.status === 204) return undefined as T
    let body: T & ApiError
    try {
      body = (await response.json()) as T & ApiError
      if (!body || typeof body !== 'object') throw new Error('invalid body')
    } catch (cause) {
      if (controller.signal.aborted) throw cause
      const failure = new RequestError(
        response.status >= 500
          ? '服务或访问通道暂时不可用，请稍后重试。'
          : '服务返回内容无效，请稍后重试。',
        response.status >= 500 ? 'service_unavailable' : 'invalid_response',
      )
      failure.status = response.status
      failure.requestId = response.headers.get('X-Request-ID')
      throw failure
    }
    if (!response.ok) {
      const failure = new RequestError(
        detailMessage(body.error?.details) || body.error?.message || '请求失败',
        body.error?.code,
        body.error?.details,
      )
      failure.status = response.status
      failure.requestId = response.headers.get('X-Request-ID')
      throw failure
    }
    return body
  } catch (cause) {
    if (signal?.aborted) throw cause
    if (timedOut)
      throw new RequestError(
        '请求超时，请检查网络和录入任务后重试。',
        'request_timeout',
      )
    if (cause instanceof RequestError) throw cause
    throw new RequestError(
      '网络连接中断或服务不可达，请检查网络和当前访问地址。',
      'network_error',
    )
  } finally {
    window.clearTimeout(timer)
    signal?.removeEventListener('abort', cancel)
  }
}

export function jsonRequest<T>(
  path: string,
  method: 'POST' | 'PUT' | 'PATCH',
  data: unknown,
): Promise<T> {
  return request<T>(path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken(),
    },
    body: JSON.stringify(data),
  })
}
