import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppError, NetworkError, RequestTimeoutError } from "./errors";
import { postAsk } from "./api";

/**
 * P0-A/P0-B contract tests for the API client.
 *
 * Two properties matter here and both were previously violated:
 *
 * 1. `postAsk` issued up to TWO attempts per question. Combined with the 8 s
 *    abort that held the browser ~16.5 s, and it re-POSTed a question whose
 *    turn had already been committed to `app.research_messages`.
 * 2. Any 200 with parseable JSON was cast to `AskResponse` and trusted, so a
 *    truncated or wrong-shaped payload rendered as a successful answer.
 */

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID = {
  request_id: "r-1",
  status: "ok",
  route: "SQLRoute",
  answer: "14 publications.",
  evidence_objects: [],
  sources: [],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("postAsk: exactly one attempt (P0-B)", () => {
  it("does not retry a 503", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ request_id: "r", error: { error_type: "internal_error", message: "x", status_code: 503 } }, 503),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(postAsk("q", false)).rejects.toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry a network failure", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(postAsk("q", false)).rejects.toBeInstanceOf(NetworkError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry a 504 llm_timeout", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ request_id: "r", error: { error_type: "llm_timeout", message: "slow", status_code: 504 } }, 504),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(postAsk("q", false)).rejects.toBeInstanceOf(RequestTimeoutError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("postAsk: response validation (P0-A)", () => {
  it("returns a well-formed envelope untouched", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(VALID)));
    await expect(postAsk("q", false)).resolves.toMatchObject({ request_id: "r-1", status: "ok" });
  });

  it("rejects a non-JSON 200 body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<html>gateway</html>", { status: 200, headers: { "Content-Type": "text/html" } }),
      ),
    );
    await expect(postAsk("q", false)).rejects.toMatchObject({ code: "MALFORMED_RESPONSE" });
  });

  it("rejects a 200 body missing required arrays", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ request_id: "r", status: "ok", route: "SQLRoute", answer: "hi" }),
      ),
    );
    await expect(postAsk("q", false)).rejects.toMatchObject({ code: "MALFORMED_RESPONSE" });
  });

  it("rejects a 200 body whose sources are not shaped like sources", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...VALID,
          sources: [{ publication_id: "p1" }], // no `title`
          evidence_objects: [],
        }),
      ),
    );
    await expect(postAsk("q", false)).rejects.toMatchObject({ code: "MALFORMED_RESPONSE" });
  });

  it("rejects a JSON array body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([1, 2, 3])));
    await expect(postAsk("q", false)).rejects.toMatchObject({ code: "MALFORMED_RESPONSE" });
  });
});

describe("postAsk: abort handling", () => {
  it("propagates an abort rather than reporting it as a backend failure", async () => {
    const ctrl = new AbortController();
    const abortErr = new Error("The operation was aborted");
    abortErr.name = "AbortError";
    const fetchMock = vi.fn().mockImplementation(() => {
      ctrl.abort();
      return Promise.reject(abortErr);
    });
    vi.stubGlobal("fetch", fetchMock);

    // The caller distinguishes "superseded" from "failed" by this.
    await expect(postAsk("q", false, ctrl.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("forwards the abort signal to fetch", async () => {
    const ctrl = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(VALID));
    vi.stubGlobal("fetch", fetchMock);

    await postAsk("q", false, ctrl.signal);
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(init.signal).toBe(ctrl.signal);
  });

  it("sends a stable X-Request-ID for the request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(VALID));
    vi.stubGlobal("fetch", fetchMock);

    await postAsk("q", false);
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Request-ID"]).toBeTruthy();
  });
});

describe("postAsk: never substitutes data", () => {
  it("rejects rather than returning a default object on failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    // There is no code path that resolves to a synthesised AskResponse.
    await expect(postAsk("q", false)).rejects.toBeInstanceOf(AppError);
  });
});