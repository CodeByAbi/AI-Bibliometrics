// Typed frontend error hierarchy — mirrors backend AppException contract.
// backend/app/core/errors.py + models/errors.py envelope:
//   { request_id, error: { error_type, message, status_code, details? } }

export class AppError extends Error {
  readonly code: string;
  readonly statusCode: number;
  readonly details?: unknown;
  readonly requestId?: string;

  constructor(message: string, code = "INTERNAL_ERROR", statusCode = 500, details?: unknown, requestId?: string) {
    super(message);
    this.name = this.constructor.name;
    Object.setPrototypeOf(this, new.target.prototype);
    this.code = code;
    this.statusCode = statusCode;
    this.details = details;
    this.requestId = requestId;
  }
}

export class NotFoundError extends AppError {
  constructor(message = "No supporting evidence found", requestId?: string) {
    super(message, "NOT_FOUND", 404, undefined, requestId);
  }
}

export class ValidationError extends AppError {
  constructor(message: string, details?: unknown, requestId?: string) {
    super(message, "VALIDATION_ERROR", 422, details, requestId);
  }
}

export class UnauthorizedError extends AppError {
  constructor(message = "Authentication required", requestId?: string) {
    super(message, "UNAUTHORIZED", 401, undefined, requestId);
  }
}

export class RateLimitError extends AppError {
  constructor(readonly retryAfterMs = 60_000, requestId?: string) {
    super("Rate limit exceeded", "RATE_LIMITED", 429, undefined, requestId);
  }
}

export class ServiceUnavailableError extends AppError {
  constructor(message = "Service temporarily unavailable", requestId?: string) {
    super(message, "SERVICE_UNAVAILABLE", 503, undefined, requestId);
  }
}

export class NetworkError extends AppError {
  constructor(message = "Network request failed", requestId?: string) {
    super(message, "NETWORK_ERROR", 0, undefined, requestId);
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

interface BackendErrorBody {
  request_id?: string;
  requestId?: string;
  error?: { error_type?: string; code?: string; message?: string; status_code?: number; statusCode?: number; details?: unknown };
}

/** Parse a non-OK fetch Response with backend envelope awareness. Never throws. */
export async function parseBackendError(res: Response, fallbackBody = ""): Promise<AppError> {
  const body = fallbackBody;
  let parsed: BackendErrorBody | null = null;
  try {
    parsed = JSON.parse(body) as BackendErrorBody;
  } catch {
    parsed = null;
  }
  const requestId = parsed?.request_id ?? parsed?.requestId;
  const err = parsed?.error;
  const code = err?.error_type ?? err?.code ?? `HTTP_${res.status}`;
  const message = err?.message ?? body.slice(0, 300) ?? res.statusText;
  const status = err?.status_code ?? err?.statusCode ?? res.status;

  switch (status) {
    case 401:
      return new UnauthorizedError(message, requestId);
    case 404:
      return new NotFoundError(message, requestId);
    case 422:
      return new ValidationError(message, err?.details, requestId);
    case 429:
      return new RateLimitError(60_000, requestId);
    case 503:
    case 502:
    case 504:
      return new ServiceUnavailableError(message, requestId);
    default:
      return new AppError(message, code, status, err?.details, requestId);
  }
}

// ------------------------------------------------------------------
// User-facing messages — technical details stay in console/server logs.
// ------------------------------------------------------------------

const USER_ERROR_MESSAGES: Record<string, string> = {
  NOT_FOUND: "No supporting evidence was found for this question.",
  not_found: "No supporting evidence was found for this question.",
  UNAUTHORIZED: "Please sign in to continue.",
  FORBIDDEN: "You don't have permission to do that.",
  VALIDATION_ERROR: "Please check your input and try again.",
  validation_error: "Please check your input and try again.",
  RATE_LIMITED: "Too many requests. Please wait a moment and try again.",
  rate_limit_exceeded: "Too many requests. Please wait a moment and try again.",
  SERVICE_UNAVAILABLE: "The database query exceeded its time limit. Narrow the year range or add a filter, then retry.",
  db_timeout: "The database query exceeded its time limit. Narrow the year range or add a filter, then retry.",
  NETWORK_ERROR: "Cannot reach the research backend. Check your connection and retry.",
  INTERNAL_ERROR: "Something went wrong on our end. Please try again later.",
  internal_error: "Something went wrong on our end. Please try again later.",
};

export function getUserMessage(code: string): string {
  return USER_ERROR_MESSAGES[code] ?? USER_ERROR_MESSAGES.INTERNAL_ERROR!;
}

// ------------------------------------------------------------------
// Retry with exponential backoff — retriable errors only (never 4xx).
// ------------------------------------------------------------------

export interface RetryOptions {
  maxAttempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  retryIf?: (error: unknown) => boolean;
}

export function defaultRetryIf(error: unknown): boolean {
  if (isAbortError(error)) return false;
  if (error instanceof AppError) {
    // Retry network faults (status 0) and 5xx/429 only — never 4xx.
    return error.statusCode === 0 || error.statusCode === 429 || error.statusCode >= 500;
  }
  return true;
}

export async function withRetry<T>(fn: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const { maxAttempts = 2, baseDelayMs = 500, maxDelayMs = 10_000, retryIf = defaultRetryIf } = options;
  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      if (attempt === maxAttempts || !retryIf(error)) throw error;
      const jitter = Math.random() * baseDelayMs;
      const delay = Math.min(baseDelayMs * 2 ** (attempt - 1) + jitter, maxDelayMs);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  throw lastError;
}
