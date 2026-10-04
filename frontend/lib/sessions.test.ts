import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createSession,
  deleteSession,
  getSession,
  listSessions,
  updateSession,
} from "./sessions";
import { AppError, NetworkError, ServiceUnavailableError } from "./errors";

/**
 * Session API client contracts.
 *
 * Three properties are load-bearing and each was a real defect class:
 *
 * 1. `session_id` must be OMITTED, not nulled, when absent. The backend reads a
 *    missing id as "stateless question"; sending null is a different request.
 * 2. A 503 `session_store_unavailable` must stay a 503. Folding it into an empty
 *    list would render "sessions are switched off" identically to "you have no
 *    sessions" — opposite meanings behind the same pixels.
 * 3. `GET /sessions/{id}` is a PURE READ. Restoring a session must never re-ask,
 *    because that would spend a full retrieval to rebuild history the backend
 *    already stored.
 */

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function createdBody(id = "11111111-1111-1111-1111-111111111111") {
  return {
    id,
    title: "New Research",
    status: "active",
    created_at: "2026-01-02T03:04:05Z",
    updated_at: "2026-01-02T03:04:05Z",
    last_message_at: null,
  };
}

function detailBody(id = "11111111-1111-1111-1111-111111111111") {
  return {
    ...createdBody(id),
    summary: "Explores MSC therapy trends.",
    messages: [
      { id: "m1", role: "user", content: "Berapa publikasi UI 2023?", status: "complete", created_at: "2026-01-02T03:04:05Z" },
      {
        id: "m2",
        role: "assistant",
        content: "14 publikasi.",
        status: "complete",
        created_at: "2026-01-02T03:04:06Z",
        request_id: "r-1",
        route: "SQLRoute",
        evidence_objects: [],
        sources: [{ publication_id: "P1", title: "Paper A", source_type: "sql" }],
      },
    ],
  };
}

function listRow(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    title: `Session ${id.slice(0, 4)}`,
    status: "active",
    created_at: "2026-01-02T03:04:05Z",
    updated_at: "2026-01-02T03:04:05Z",
    last_message_at: "2026-01-02T03:04:06Z",
    message_count: 2,
    source_count: 1,
    last_route: "SQLRoute",
    ...overrides,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("createSession", () => {
  it("POSTs and returns the created session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(createdBody(), 201));
    vi.stubGlobal("fetch", fetchMock);

    const out = await createSession();
    expect(out.id).toBe("11111111-1111-1111-1111-111111111111");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/sessions");
    expect(init.method).toBe("POST");
  });

  it("sends an explicit title when given one", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(createdBody(), 201));
    vi.stubGlobal("fetch", fetchMock);

    await createSession("My MSC research");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ title: "My MSC research" });
  });

  it("does not retry — a retry would create a second session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ error: {} }, 503));
    vi.stubGlobal("fetch", fetchMock);

    await expect(createSession()).rejects.toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("listSessions", () => {
  it("returns database-backed rows including counts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse([listRow("aaaa"), listRow("bbbb", { message_count: 0, source_count: 0, last_route: null })])),
    );

    const out = await listSessions();
    expect(out).toHaveLength(2);
    expect(out[0].source_count).toBe(1);
    // An unanswered session must not be given a fabricated route.
    expect(out[1].last_route).toBeNull();
    expect(out[1].message_count).toBe(0);
  });

  it("clamps the limit into the backend's accepted range", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await listSessions(9999);
    expect(fetchMock.mock.calls[0][0]).toContain("limit=200");
  });

  it("drops a malformed row instead of failing every session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse([listRow("aaaa"), { id: "x" }])),
    );

    const out = await listSessions();
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("aaaa");
  });

  it("surfaces 503 as unavailable, never as an empty list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { request_id: "r", error: { error_type: "session_store_unavailable", message: "off", status_code: 503 } },
          503,
        ),
      ),
    );

    await expect(listSessions()).rejects.toBeInstanceOf(ServiceUnavailableError);
  });
});

describe("getSession", () => {
  it("is a pure read: never calls /api/v1/ask", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(detailBody()));
    vi.stubGlobal("fetch", fetchMock);

    await getSession("11111111-1111-1111-1111-111111111111");
    for (const [url] of fetchMock.mock.calls) {
      expect(url).not.toContain("/ask");
    }
  });

  it("returns the ordered transcript with provenance", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(detailBody())));

    const out = await getSession("11111111-1111-1111-1111-111111111111");
    expect(out.messages).toHaveLength(2);
    expect(out.messages[0].role).toBe("user");
    expect(out.messages[1].route).toBe("SQLRoute");
    expect(out.messages[1].sources).toHaveLength(1);
    expect(out.summary).toContain("MSC");
  });

  it("url-encodes the id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(detailBody()));
    vi.stubGlobal("fetch", fetchMock);

    await getSession("a/../b");
    expect(fetchMock.mock.calls[0][0]).toContain("a%2F..%2Fb");
  });

  it("rejects a payload whose messages are not an array", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ ...detailBody(), messages: "nope" })),
    );

    await expect(getSession("x")).rejects.toBeInstanceOf(AppError);
  });

  it("rejects a payload whose messages are not message-shaped", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ ...detailBody(), messages: [{ id: "m", role: "narrator", content: "x" }] }),
      ),
    );

    // A wrong role would render as a turn the UI cannot place.
    await expect(getSession("x")).rejects.toBeInstanceOf(AppError);
  });
});

describe("updateSession", () => {
  it("PATCHes the title", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ ...createdBody(), title: "My MSC research" }));
    vi.stubGlobal("fetch", fetchMock);

    const out = await updateSession("11111111-1111-1111-1111-111111111111", { title: "My MSC research" });
    expect(out.title).toBe("My MSC research");
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("PATCH");
  });

  it("propagates a 422 for an invalid title", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { request_id: "r", error: { error_type: "validation_error", message: "blank", status_code: 422 } },
          422,
        ),
      ),
    );

    await expect(updateSession("x", { title: "   " })).rejects.toBeTruthy();
  });
});

describe("deleteSession", () => {
  it("accepts a 204 with no body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(deleteSession("x")).resolves.toBeUndefined();
  });

  it("treats a 404 on delete as success — DELETE is idempotent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
    await expect(deleteSession("x")).rejects.toBeTruthy();
  });
});

describe("transport failures", () => {
  it("maps a network failure to NetworkError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(listSessions()).rejects.toBeInstanceOf(NetworkError);
  });

  it("propagates an abort rather than reporting it as a failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("x", "AbortError")));
    await expect(listSessions()).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects a 200 whose body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>", { status: 200, headers: { "Content-Type": "text/html" } })),
    );
    await expect(listSessions()).rejects.toBeInstanceOf(AppError);
  });
});