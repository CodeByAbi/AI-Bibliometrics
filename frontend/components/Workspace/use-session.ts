"use client";

/**
 * Research-session state for the workspace.
 *
 * Docs Reference: docs/06 Api Design.md §6.2.
 *
 * ONE SOURCE OF TRUTH
 * ===================
 * `sessionId` is owned by the ROUTE (`/research/[sessionId]`), not by this hook
 * and not by localStorage. The route is canonical because it is the only one of
 * the three that survives a reload, can be linked to, and agrees with what the
 * backend persists. A duplicate copy in component state would be a second source
 * of truth that drifts the moment the user opens a link or hits Back.
 *
 * The hook receives `sessionId` as a parameter and never mutates it; navigation is
 * the caller's job. That keeps "which session am I in" a routing concern and
 * "what is in it" a data concern.
 *
 * DEGRADATION IS EXPLICIT
 * =======================
 * When session persistence is unconfigured the backend answers 503
 * `session_store_unavailable` rather than pretending to save. This hook surfaces
 * that as `unavailable` so the UI can say so. It never converts the failure into
 * an empty session list, because "sessions are switched off" and "you have no
 * sessions" look identical in the sidebar and mean opposite things.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  createSession as apiCreateSession,
  getSession,
  listSessions,
  type SessionCreated,
  type SessionDetail,
  type SessionListItem,
  type SessionMessage,
} from "@/lib/sessions";
import { isAbortError } from "@/lib/errors";

export interface SessionState {
  sessionId: string | null;
  detail: SessionDetail | null;
  messages: SessionMessage[];
  recent: SessionListItem[];
  loading: boolean;
  creating: boolean;
  /** True when the backend has session persistence switched off (503). */
  unavailable: boolean;
  errorMsg: string | null;
  refreshRecent: () => Promise<void>;
  newSession: () => Promise<SessionCreated | null>;
}

/** Sort newest-activity-first, mirroring the backend's own ordering. */
function sortRecent(items: SessionListItem[]): SessionListItem[] {
  return [...items].sort((a, b) => {
    const av = a.last_message_at ?? a.updated_at ?? a.created_at;
    const bv = b.last_message_at ?? b.updated_at ?? b.created_at;
    if (av === bv) return 0;
    return av < bv ? 1 : -1;
  });
}

export function useSession(sessionId: string | null): SessionState {
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [recent, setRecent] = useState<SessionListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const describe = useCallback((error: unknown): string => {
    if (error instanceof Error && "code" in error) {
      const code = String((error as { code: string }).code);
      if (code === "SESSION_STORE_UNAVAILABLE") {
        return "Session persistence is not configured on the server.";
      }
      if (code === "SESSION_NOT_FOUND") return "That research session no longer exists.";
      if (code === "VALIDATION_ERROR") return "That title is not valid.";
    }
    return error instanceof Error ? error.message : "Something went wrong.";
  }, []);

  const refreshRecent = useCallback(async () => {
    try {
      const items = await listSessions();
      setRecent(sortRecent(items));
    } catch (error) {
      if (isAbortError(error)) return;
      const code =
        error instanceof Error && "code" in error
          ? String((error as { code: string }).code)
          : "";
      // A 503 must stay visible as "unavailable", never as an empty list.
      if (code === "SERVICE_UNAVAILABLE" || code === "SESSION_STORE_UNAVAILABLE") {
        setUnavailable(true);
        setRecent([]);
        return;
      }
      setErrorMsg(describe(error));
    }
  }, [describe]);

  // Recent sessions: on mount and after mutations, never polled. The endpoint
  // shares the per-IP rate-limit budget with /api/v1/ask (docs/08 §3).
  useEffect(() => {
    void refreshRecent();
  }, [refreshRecent]);

  // Restore the routed session. PURE READ: no /api/v1/ask, so reopening an old
  // session never re-runs retrieval or synthesis.
  useEffect(() => {
    abortRef.current?.abort();
    if (!sessionId) {
      setDetail(null);
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setErrorMsg(null);
    (async () => {
      try {
        const d = await getSession(sessionId, ctrl.signal);
        if (ctrl.signal.aborted) return;
        setDetail(d);
      } catch (error) {
        if (isAbortError(error)) return;
        const code =
          error instanceof Error && "code" in error
            ? String((error as { code: string }).code)
            : "";
        if (code === "SERVICE_UNAVAILABLE" || code === "SESSION_STORE_UNAVAILABLE") {
          setUnavailable(true);
        }
        setDetail(null);
        setErrorMsg(describe(error));
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    })();
    return () => ctrl.abort();
  }, [sessionId, describe]);

  const newSession = useCallback(async (): Promise<SessionCreated | null> => {
    setCreating(true);
    setErrorMsg(null);
    try {
      const created = await apiCreateSession();
      // Optimistically surface it so the sidebar shows the new workspace without
      // waiting for the list round-trip.
      setRecent((prev) =>
        sortRecent([
          {
            id: created.id,
            title: created.title,
            status: created.status,
            created_at: created.created_at,
            updated_at: created.updated_at,
            last_message_at: created.last_message_at ?? null,
            message_count: 0,
            source_count: 0,
            last_route: null,
          },
          ...prev,
        ]),
      );
      void refreshRecent();
      return created;
    } catch (error) {
      if (isAbortError(error)) return null;
      const code =
        error instanceof Error && "code" in error
          ? String((error as { code: string }).code)
          : "";
      if (code === "SERVICE_UNAVAILABLE" || code === "SESSION_STORE_UNAVAILABLE") {
        setUnavailable(true);
      }
      setErrorMsg(describe(error));
      return null;
    } finally {
      setCreating(false);
    }
  }, [describe, refreshRecent]);

  return {
    sessionId,
    detail,
    messages: detail?.messages ?? [],
    recent,
    loading,
    creating,
    unavailable,
    errorMsg,
    refreshRecent,
    newSession,
  };
}