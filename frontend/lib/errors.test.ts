import { describe, expect, it, vi } from "vitest";
import {
  AppError,
  NetworkError,
  NotFoundError,
  RateLimitError,
  ServiceUnavailableError,
  UnauthorizedError,
  ValidationError,
  defaultRetryIf,
  getUserMessage,
  isAbortError,
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

  it("maps 502/503/504 to ServiceUnavailableError", async () => {
    for (const status of [502, 503, 504]) {
      expect(await parse(status, {})).toBeInstanceOf(ServiceUnavailableError);
    }
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
});