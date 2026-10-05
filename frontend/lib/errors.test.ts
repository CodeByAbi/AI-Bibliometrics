import { describe, expect, it, vi } from "vitest";
import {
  AppError,
  NetworkError,
  NotFoundError,
  RateLimitError,
  RequestTimeoutError,
  ServiceUnavailableError,
  UnauthorizedError,
  ValidationError,
  defaultRetryIf,
  getUserMessage,
  isAbortError,
  isTimeoutError,
  parseBackendError,
  withRetry,
} from "./errors";

// postAsk already consumed the body via res.text() and passes the string in as
// `fallbackBody`, so the helper mirrors that call shape exactly.
const jsonResponse = (status: number, body: unknown): Response =>
  ({ ok: false, status, statusText: "x" }) as unknown as Response;

const bodyOf = (body: unknown): string => JSON.stringify(body);

const parse = (status: number, body: unknown) => parseBackendError(jsonResponse(status, body), bodyOf(body));

describe("typed error hierarchy", () => {
  it("preserves the code and status through subclasses", () => {
    expect(new NotFoundError().code).toBe("NOT_FOUND");
    expect(new NotFoundError().statusCode).toBe(404);
    expect(new ValidationError("bad").code).toBe("VALIDATION_ERROR");
    expect(new UnauthorizedError().statusCode).toBe(401);
    expect(new RateLimitError(1200).retryAfterMs).toBe(1200);
    expect(new ServiceUnavailableError().statusCode).toBe(503);
    expect(new NetworkError().statusCode).toBe(0);
  });

  it("is a real Error subclass, so instanceof survives", () => {
    const e = new NotFoundError();
    expect(e).toBeInstanceOf(AppError);
    expect(e).toBeInstanceOf(Error);
    expect(e.name).toBe("NotFoundError");
  });
});

describe("parseBackendError", () => {
  it("maps 422 and forwards validation details", async () => {
    const err = await parse(422, {
      request_id: "r1",
      error: { error_type: "VALIDATION_ERROR", message: "nope", details: { field: "year" } },
    });
    expect(err).toBeInstanceOf(ValidationError);
    expect(err.requestId).toBe("r1");
    expect(err.details).toEqual({ field: "year" });
  });

  it("maps 429 to RateLimitError", async () => {
    expect(await parse(429, {})).toBeInstanceOf(RateLimitError);
  });

  it("maps 502/503 to ServiceUnavailableError", async () => {
    for (const status of [502, 503]) {
      expect(await parse(status, {})).toBeInstanceOf(ServiceUnavailableError);
    }
  });

  it("maps a bare 504 to a timeout-class error, not ServiceUnavailable (P0-B)", async () => {
    // 504 Gateway Timeout means an upstream did not answer in time. The UI
    // has to be able to say so, so it must not arrive as a generic
    // "service unavailable".
    const err = await parse(504, {});
    expect(isTimeoutError(err)).toBe(true);
  });

  it("maps backend 504 llm_timeout to RequestTimeoutError (P0-B contract)", async () => {
    const err = await parse(504, {
      error: { error_type: "llm_timeout", message: "generator too slow", status_code: 504 },
    });
    expect(err).toBeInstanceOf(RequestTimeoutError);
    expect(err.code).toBe("REQUEST_TIMEOUT");
    expect(err.statusCode).toBe(504);
  });

  it("maps backend 503 db_timeout to a timeout-class error", async () => {
    const err = await parse(503, {
      error: { error_type: "db_timeout", message: "statement timeout", status_code: 503 },
    });
    expect(isTimeoutError(err)).toBe(true);
  });

  it("keeps 503 session_store_unavailable as ServiceUnavailable, not a timeout", async () => {
    const err = await parse(503, {
      error: { error_type: "session_store_unavailable", message: "not configured", status_code: 503 },
    });
    expect(err).toBeInstanceOf(ServiceUnavailableError);
    expect(isTimeoutError(err)).toBe(false);
  });

  it("keeps 422 sql_generation_failed distinct from a timeout", async () => {
    const err = await parse(422, {
      error: { error_type: "sql_generation_failed", message: "unsafe query", status_code: 422 },
    });
    expect(err).toBeInstanceOf(ValidationError);
    expect(isTimeoutError(err)).toBe(false);
  });

  it("falls back to an HTTP_<status> code when the body carries none", async () => {
    const err = await parse(418, {});
    expect(err.code).toBe("HTTP_418");
  });

  it("never throws on a non-JSON body", async () => {
    const res = { ok: false, status: 500, statusText: "boom", text: async () => "<html>oops</html>" } as unknown as Response;
    const err = await parseBackendError(res, "<html>oops</html>");
    expect(err).toBeInstanceOf(AppError);
    expect(err.message).toContain("oops");
  });
});

describe("getUserMessage", () => {
  it("maps known codes to non-technical copy", () => {
    expect(getUserMessage("NOT_FOUND")).toMatch(/no supporting evidence/i);
    expect(getUserMessage("RATE_LIMITED")).toMatch(/too many requests/i);
  });

  it("never leaks a raw code for an unknown one", () => {
    expect(getUserMessage("SOMETHING_NEW")).toBe(getUserMessage("INTERNAL_ERROR"));
  });
});

describe("isAbortError", () => {
  it("recognizes an AbortError DOMException", () => {
    expect(isAbortError(new DOMException("aborted", "AbortError"))).toBe(true);
  });

  it("rejects an ordinary error", () => {
    expect(isAbortError(new Error("nope"))).toBe(false);
  });
});

describe("defaultRetryIf", () => {
  it("retries network faults, 429, and 5xx", () => {
    expect(defaultRetryIf(new NetworkError())).toBe(true);
    expect(defaultRetryIf(new RateLimitError())).toBe(true);
    expect(defaultRetryIf(new ServiceUnavailableError())).toBe(true);
  });

  it("never retries a 4xx", () => {
    expect(defaultRetryIf(new ValidationError("bad"))).toBe(false);
    expect(defaultRetryIf(new UnauthorizedError())).toBe(false);
    expect(defaultRetryIf(new NotFoundError())).toBe(false);
  });

  it("never retries an abort", () => {
    expect(defaultRetryIf(new DOMException("x", "AbortError"))).toBe(false);
    // Node/undici and several test environments reject with a plain Error
    // carrying name="AbortError" rather than a DOMException.
    const err = new Error("The operation was aborted");
    err.name = "AbortError";
    expect(defaultRetryIf(err)).toBe(false);
  });

  it("never retries a timeout (P0-B)", () => {
    // A 504 means the upstream already spent its budget. Retrying doubles the
    // wall clock the user waits without changing the outcome.
    expect(defaultRetryIf(new RequestTimeoutError())).toBe(false);
  });
});

describe("isAbortError", () => {
  it("matches a DOMException AbortError", () => {
    expect(isAbortError(new DOMException("x", "AbortError"))).toBe(true);
  });

  it("matches a plain Error named AbortError", () => {
    const err = new Error("aborted");
    err.name = "AbortError";
    expect(isAbortError(err)).toBe(true);
  });

  it("does not match unrelated errors or non-errors", () => {
    expect(isAbortError(new Error("network down"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
    expect(isAbortError(undefined)).toBe(false);
    expect(isAbortError("AbortError")).toBe(false);
  });
});

describe("isTimeoutError", () => {
  it("recognises the timeout taxonomy on both sides of the wire", () => {
    expect(isTimeoutError(new RequestTimeoutError())).toBe(true);
    expect(isTimeoutError(new AppError("m", "llm_timeout", 504))).toBe(true);
    expect(isTimeoutError(new AppError("m", "db_timeout", 503))).toBe(true);
  });

  it("does not classify ordinary failures as timeouts", () => {
    expect(isTimeoutError(new NetworkError())).toBe(false);
    expect(isTimeoutError(new ServiceUnavailableError())).toBe(false);
    expect(isTimeoutError(new ValidationError("bad"))).toBe(false);
  });
});

describe("withRetry", () => {
  it("returns the first successful result without retrying", async () => {
    const fn = vi.fn().mockResolvedValue("ok");
    await expect(withRetry(fn)).resolves.toBe("ok");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("retries a transient failure then succeeds", async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new ServiceUnavailableError())
      .mockResolvedValue("ok");
    await expect(withRetry(fn, { baseDelayMs: 1 })).resolves.toBe("ok");
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("gives up after maxAttempts and rethrows the last error", async () => {
    const fn = vi.fn().mockRejectedValue(new ServiceUnavailableError());
    await expect(withRetry(fn, { maxAttempts: 3, baseDelayMs: 1 })).rejects.toBeInstanceOf(ServiceUnavailableError);
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("does not retry a non-retriable error", async () => {
    const fn = vi.fn().mockRejectedValue(new ValidationError("bad"));
    await expect(withRetry(fn, { baseDelayMs: 1 })).rejects.toBeInstanceOf(ValidationError);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("stops retrying once the caller's signal is aborted (P0-B)", async () => {
    // The supersede case: the user typed a new question, which aborts the
    // in-flight POST. Without the signal check the loop fires a SECOND POST
    // for a question the user already replaced — and because the backend
    // commits the user turn before retrieval, that duplicates the question in
    // the conversation transcript.
    const ctrl = new AbortController();
    const fn = vi.fn().mockImplementation(async () => {
      ctrl.abort();
      throw new ServiceUnavailableError();
    });
    await expect(withRetry(fn, { maxAttempts: 3, baseDelayMs: 1, signal: ctrl.signal })).rejects.toBeTruthy();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("does not retry a timeout (P0-B)", async () => {
    const fn = vi.fn().mockRejectedValue(new RequestTimeoutError());
    await expect(withRetry(fn, { maxAttempts: 3, baseDelayMs: 1 })).rejects.toBeInstanceOf(RequestTimeoutError);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});