/**
 * Session API client — the research-workspace persistence surface.
 *
 * Docs Reference: docs/06 Api Design.md §6.2, docs/03 System Architecture.md §0.3.
 *
 * WHAT THIS IS
 * ============
 * Conversation-state CRUD for the workspace: create a session, list recent ones,
 * restore one with its full transcript, rename it, delete it.
 *
 * WHAT THIS IS NOT
 * ================
 * Not a retrieval surface. Nothing here returns a bibliometric metric, and
 * nothing here can be used to obtain one. `SessionMessageResponse` carries
 * `evidence_objects` / `sources` so a restored turn can redraw the evidence rail
 * it was answered with — but those are a RENDERING SNAPSHOT of a past response,
 * never a source of truth. Answering a question goes through `postAsk`, which
 * re-queries the canonical corpus. `request_id` on each stored turn is the
 * re-verification key.
 *
 * The same split is why `listSessions` asks the database for `message_count`,
 * `source_count` and `last_route` rather than deriving them client-side from a
 * transcript it never fetched. The database is authoritative for a sidebar.
 *
 * ERROR BEHAVIOUR
 * ===============
 * Every function reuses `parseBackendError`, so a session failure surfaces as the
 * same `AppError` taxonomy the ask path already produces. That matters most for
 * `session_store_unavailable` (HTTP 503): when session persistence is not
 * configured, the backend says so explicitly rather than silently dropping the
 * session. The UI must render that as "sessions unavailable", never as an empty
 * list that looks like a working feature with no history.
 */

import { API_BASE, postAsk, type AskResponse } from "@/lib/api";
import {
  AppError,
  NetworkError,
  isAbortError,
  parseBackendError,
} from "@/lib/errors";

export type MessageRole = "user" | "assistant";
export type MessageStatus = "complete" | "failed";
export type SessionStatus = "active" | "archived";

/** One stored turn, as returned by `GET /api/v1/sessions/{id}`. */
export interface SessionMessage {
  id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  created_at: string;
  request_id?: string | null;
  route?: string | null;
  /**
   * Rendering provenance for this turn (migration 006). An immutable snapshot of
   * what the verified response carried — never a source of truth, and never a
   * basis for answering a bibliometric question.
   */
  evidence_objects?: EvidenceObject[];
  sources?: SourceItem[];
}

/** Minimal structural shape; the concrete types are reused from `lib/api`. */
type EvidenceObject = AskResponse["evidence_objects"][number];
type SourceItem = AskResponse["sources"][number];

/** `POST /api/v1/sessions` response. */
export interface SessionCreated {
  id: string;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
  last_message_at?: string | null;
}

/**
 * One row of `GET /api/v1/sessions`.
 *
 * `message_count` / `source_count` / `last_route` are computed in SQL. They are
 * metadata, not transcript, so the list endpoint stays cheap with many sessions.
 */
export interface SessionListItem {
  id: string;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
  last_message_at?: string | null;
  message_count: number;
  source_count: number;
  last_route?: string | null;
}

/** `GET /api/v1/sessions/{id}` response: metadata plus the ordered transcript. */
export interface SessionDetail extends SessionCreated {
  summary?: string | null;
  messages: SessionMessage[];
}

function newRequestId(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Shared request path for the session endpoints.
 *
 * Same single-attempt policy as `postAsk`: the backend returns structured 4xx/5xx
 * envelopes, so retrying only adds wall clock. And for POST /sessions a retry is
 * actively wrong — it would create a second session for one user action.
 */
async function sessionFetch<T>(
  path: string,
  init: RequestInit & { signal?: AbortSignal },
  validate?: (v: unknown) => v is T,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": newRequestId(),
        ...(init.headers ?? {}),
      },
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new NetworkError(
      error instanceof Error ? `Backend unreachable: ${error.message}` : "Backend unreachable",
    );
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw await parseBackendError(res, text);
  }
  // 204 No Content: DELETE returns no body, so there is nothing to parse. The
  // validator is omitted for that case rather than faked.
  if (res.status === 204) return undefined as T;
  let parsed: unknown;
  try {
    parsed = await res.json();
  } catch {
    throw new AppError(
      "The backend returned a response that could not be read.",
      "MALFORMED_RESPONSE",
      502,
    );
  }
  if (validate && !validate(parsed)) {
    throw new AppError(
      "The backend returned an unexpected response shape.",
      "MALFORMED_RESPONSE",
      502,
    );
  }
  return parsed as T;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function isSessionCreated(v: unknown): v is SessionCreated {
  if (!isRecord(v)) return false;
  return typeof v.id === "string" && typeof v.title === "string";
}

function isSessionListItem(v: unknown): v is SessionListItem {
  if (!isRecord(v)) return false;
  return (
    typeof v.id === "string" &&
    typeof v.title === "string" &&
    typeof v.message_count === "number" &&
    typeof v.source_count === "number"
  );
}

function isSessionMessage(v: unknown): v is SessionMessage {
  if (!isRecord(v)) return false;
  return (
    typeof v.id === "string" &&
    (v.role === "user" || v.role === "assistant") &&
    typeof v.content === "string"
  );
}

function isSessionDetail(v: unknown): v is SessionDetail {
  if (!isRecord(v)) return false;
  if (!isSessionCreated(v)) return false;
  // `messages` must be an array OF MESSAGES. A non-array here would render as a
  // silently empty transcript, which reads as "this session had no turns".
  return Array.isArray(v.messages) && v.messages.every(isSessionMessage);
}

/**
 * Create a research session.
 *
 * Called by the New Research action so the workspace has a real persisted
 * identity BEFORE the first question. Creating it lazily on first ask would leave
 * a user who clicks New Research and then navigates away with no record, and
 * would make the route carry no id until it was too late to be meaningful.
 */
export async function createSession(
  title?: string,
  signal?: AbortSignal,
): Promise<SessionCreated> {
  return sessionFetch<SessionCreated>(
    "/api/v1/sessions",
    {
      method: "POST",
      body: JSON.stringify(title ? { title } : {}),
      signal,
    },
    isSessionCreated,
  );
}

/**
 * List recent sessions, newest activity first.
 *
 * Not polled: `/api/v1/sessions` shares the per-IP rate-limit budget with
 * `/api/v1/ask` (docs/08 §3), so the UI fetches on mount and after each mutation
 * rather than on a timer.
 */
export async function listSessions(
  limit = 20,
  signal?: AbortSignal,
): Promise<SessionListItem[]> {
  const raw = await sessionFetch<unknown>(
    `/api/v1/sessions?limit=${Math.max(1, Math.min(limit, 200))}`,
    { method: "GET", signal },
    (v): v is unknown => Array.isArray(v),
  );
  const items = raw as unknown[];
  // Filter rather than reject: one malformed row must not hide every other
  // session from the sidebar.
  return items.filter(isSessionListItem);
}

/**
 * Restore a session with its full transcript.
 *
 * A PURE READ. It never re-runs the RAG pipeline: reopening an old session must
 * not re-embed a query, re-retrieve, or re-synthesise an answer that was already
 * delivered and verified. The restored evidence and sources come from the stored
 * per-turn snapshot.
 */
export async function getSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionDetail> {
  return sessionFetch<SessionDetail>(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}`,
    { method: "GET", signal },
    isSessionDetail,
  );
}

/** Rename a session. Rejects a blank or oversized title with 422. */
export async function updateSession(
  sessionId: string,
  payload: { title: string },
  signal?: AbortSignal,
): Promise<SessionCreated> {
  return sessionFetch<SessionCreated>(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}`,
    { method: "PATCH", body: JSON.stringify(payload), signal },
    isSessionCreated,
  );
}

/** Delete a session and its transcript. 204 whether or not it existed. */
export async function deleteSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<void> {
  await sessionFetch<void>(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE", signal },
  );
}

export { postAsk };