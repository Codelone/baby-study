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

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/baby/study' + path, {
    credentials: 'same-origin',
    ...init,
  })
  if (response.status === 204) return undefined as T
  const body = (await response.json()) as T & ApiError
  if (!response.ok) {
    throw new RequestError(
      detailMessage(body.error?.details) || body.error?.message || '请求失败',
      body.error?.code,
      body.error?.details,
    )
  }
  return body
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
