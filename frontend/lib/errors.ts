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

/**
 * A timeout, from either side of the wire.
 *
 * P0-A/P0-B: kept distinct from AppError-generic failure so the UI can say
 * "the model was too slow" rather than "something went wrong". Backend
 * `llm_timeout` (504, raised when Ollama Text-to-SQL exceeds
 * TEXT2SQL_TIMEOUT_S) and a client-side abort both land here.
 */
export class RequestTimeoutError extends AppError {
  constructor(message = "The request timed out", requestId?: string) {
    super(message, "REQUEST_TIMEOUT", 504, undefined, requestId);
  }
}

export class NetworkError extends AppError {
  constructor(message = "Network request failed", requestId?: string) {
    super(message, "NETWORK_ERROR", 0, undefined, requestId);
  }
}

/**
 * True for a cancelled request.
 *
 * Checks both `DOMException` and a plain `Error` carrying `name: "AbortError"`:
 * the DOM spec says `fetch` rejects with a DOMException, but Node's fetch,
 * undici, and several test environments throw an ordinary Error instead. The
 * old instanceof-only check let those through as genuine failures, which is how
 * a supersede-abort could be reported to the user as a backend error.
 */
export function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") return true;
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

/** True when an error means "the request ran out of time" on either side. */
export function isTimeoutError(error: unknown): boolean {
  if (error instanceof RequestTimeoutError) return true;
  const code = error instanceof AppError ? error.code : undefined;
  return code === "REQUEST_TIMEOUT" || code === "llm_timeout" || code === "db_timeout";
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
    case 504:
      // 504 Gateway Timeout is ALWAYS a timeout, whether or not the body
      // carries a code. The upstream did not answer in time; that is the whole
      // meaning of the status, and reporting it as a generic error or as
      // "service unavailable" hides the only useful fact. `llm_timeout` keeps
      // its own code so the copy can name the generator specifically.
      return new RequestTimeoutError(message, requestId);
    case 503:
      return code === "db_timeout"
        ? new RequestTimeoutError(message, requestId)
        : new ServiceUnavailableError(message, requestId);
    case 502:
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
  SERVICE_UNAVAILABLE: "The service is temporarily unavailable. Please retry in a moment.",
  // P0-A: a timeout says what actually happened. It is never dressed up as a
  // generic failure and it is never answered with placeholder data.
  REQUEST_TIMEOUT:
    "The request timed out before the research backend answered. Narrow the year range or add an author or institution filter, then retry.",
  llm_timeout:
    "The query generator did not respond in time for this phrasing. Rephrase the question more specifically, or ask about a supported aggregate.",
  db_timeout:
    "The database query exceeded its time limit. Narrow the year range or add a filter, then retry.",
  // P0-3: the backend's 422 for a question that cannot be safely expressed as
  // SQL. Named explicitly so it is not confused with a timeout.
  sql_generation_failed:
    "This question could not be turned into a safe database query. Rephrase it as a count, ranking, or publication list.",
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
  /**
   * Abort signal of the caller's request.
   *
   * P0-B: without it the retry loop can re-issue a request the user already
   * superseded. `fetch` rejects with AbortError on the in-flight call, but the
   * loop treated that as an ordinary transient failure and fired a second POST
   * — which also re-sent a question whose turn had already been committed to
   * `app.research_messages`.
   */
  signal?: AbortSignal;
}

export function defaultRetryIf(error: unknown): boolean {
  if (isAbortError(error)) return false;
  if (error instanceof AppError) {
    // Retry network faults (status 0) and 5xx/429 only — never 4xx, and never
    // a timeout: a 504 means the upstream already spent its budget, and asking
    // again just doubles the wall clock the user waits.
    if (isTimeoutError(error)) return false;
    return error.statusCode === 0 || error.statusCode === 429 || error.statusCode >= 500;
  }
  return true;
}

export async function withRetry<T>(fn: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const {
    maxAttempts = 2,
    baseDelayMs = 500,
    maxDelayMs = 10_000,
    retryIf = defaultRetryIf,
    signal,
  } = options;
  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    // Between attempts the caller may have aborted (new question typed, view
    // unmounted, or the abort timeout fired). Bail rather than re-POST.
    if (signal?.aborted) throw lastError ?? new DOMException("Aborted", "AbortError");
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      if (attempt === maxAttempts || signal?.aborted || !retryIf(error)) throw error;
      const jitter = Math.random() * baseDelayMs;
      const delay = Math.min(baseDelayMs * 2 ** (attempt - 1) + jitter, maxDelayMs);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  throw lastError;
}
