import type { ApiErrorBody } from './types'

export const API_MODE = import.meta.env.VITE_API_MODE === 'real' ? 'real' : 'mock'
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')

export class ApiError extends Error {
  readonly code: string
  readonly requestId: string
  readonly status: number
  readonly fieldErrors: ApiErrorBody['field_errors']
  readonly retryAfterSeconds: ApiErrorBody['retry_after_seconds']

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.requestId = body.request_id
    this.fieldErrors = body.field_errors
    this.retryAfterSeconds = body.retry_after_seconds
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  idempotencyKey?: string
  csrf?: boolean
}

type AuthRequiredHandler = () => void
let authRequiredHandler: AuthRequiredHandler | undefined

export function setAuthRequiredHandler(handler: AuthRequiredHandler | undefined): void {
  authRequiredHandler = handler
}

export function notifyAuthRequired(): void {
  try { authRequiredHandler?.() } catch { /* Session cleanup must not hide the API error. */ }
}

function cookieValue(name: string): string | undefined {
  const encoded = document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name}=`))
    ?.slice(name.length + 1)
  if (encoded === undefined) return undefined
  try {
    return decodeURIComponent(encoded)
  } catch {
    return encoded
  }
}

function normalizeErrorBody(value: unknown, fallback: ApiErrorBody): ApiErrorBody {
  if (!value || typeof value !== 'object') return fallback
  const candidate = value as Partial<ApiErrorBody>
  if (typeof candidate.code !== 'string' || typeof candidate.message !== 'string') return fallback
  return {
    code: candidate.code,
    message: candidate.message,
    request_id: typeof candidate.request_id === 'string' ? candidate.request_id : fallback.request_id,
    field_errors: Array.isArray(candidate.field_errors) ? candidate.field_errors : undefined,
    retry_after_seconds: typeof candidate.retry_after_seconds === 'number' ? candidate.retry_after_seconds : undefined,
  }
}

async function responseError(response: Response, fallbackMessage: string): Promise<ApiError> {
  const fallback: ApiErrorBody = {
    code: 'INTERNAL_ERROR',
    message: fallbackMessage,
    request_id: response.headers.get('X-Request-ID') ?? 'unknown',
  }
  const parsed = await response.json().catch(() => undefined)
  const body = normalizeErrorBody(parsed, fallback)
  if (body.code === 'AUTH_REQUIRED') notifyAuthRequired()
  return new ApiError(response.status, body)
}

export async function httpRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers = new Headers({ Accept: 'application/json' })
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')
  if (options.csrf ?? method !== 'GET') {
    const csrf = cookieValue('yb_csrf')
    if (csrf) headers.set('X-CSRF-Token', csrf)
  }
  if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey)

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: 'include',
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })

  if (!response.ok) throw await responseError(response, '请求未完成，请稍后重试。')
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function httpTextRequest(path: string): Promise<string> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: 'text/html' },
    credentials: 'include',
  })
  if (!response.ok) throw await responseError(response, '文档预览未能加载。')
  return response.text()
}

export function createIdempotencyKey(): string {
  return crypto.randomUUID()
}
