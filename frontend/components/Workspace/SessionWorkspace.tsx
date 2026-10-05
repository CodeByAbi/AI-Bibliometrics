"use client";

/**
 * Session-aware research workspace — the `/research/{sessionId}` shell.
 *
 * Docs Reference: docs/06 Api Design.md §6.2.
 *
 * Composition, not duplication: the live ask experience is the SAME `Workspace`
 * the stateless route uses, with two session concerns injected. Restored history
 * renders above it through `TranscriptView`. No RAG logic is reimplemented here.
 *
 *   route (canonical session id)
 *     -> useSession        GET /api/v1/sessions/{id}   (pure read, no RAG)
 *     -> TranscriptView    prior turns + their evidence/sources
 *     -> Workspace         the next live question, carrying the same session_id
 *
 * The id in the URL is the only source of truth. There is no localStorage copy:
 * a second copy would drift the moment the user shares a link or presses Back.
 */

import { useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import Workspace from "./Workspace";
import { TranscriptView } from "./TranscriptView";
import { useSession } from "./use-session";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function SessionWorkspace({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  // Validate before spending a request. A malformed id is a 422-shaped mistake,
  // not a missing session, and conflating them would report "no such session"
  // for what is really a bad URL.
  const valid = useMemo(() => UUID_RE.test(sessionId), [sessionId]);
  const session = useSession(valid ? sessionId : null);

  const handleNewResearch = useCallback(() => {
    void (async () => {
      const created = await session.newSession();
      if (created) router.push(`/research/${created.id}`);
    })();
  }, [router, session]);

  // Opening a session navigates; it never re-asks the question. The old
  // sidebar re-issued a canned query per entry, which spent a full retrieval to
  // rebuild history the backend already had.
  const handleOpenSession = useCallback(
    (id: string) => {
      if (id !== sessionId) router.push(`/research/${id}`);
    },
    [router, sessionId],
  );

  if (!valid) {
    return (
      <main className="app-top page-enter session-invalid">
        <h1>That link is not a research session</h1>
        <p className="mono">{sessionId}</p>
        <p>
          A session id is a UUID. This link does not carry one, so there is
          nothing to restore.
        </p>
        <Link className="btn-new" href="/">
          Back to research
        </Link>
      </main>
    );
  }

  const missing = session.errorMsg?.includes("no longer exists") ?? false;

  return (
    <>
      {session.unavailable && (
        <div className="session-banner" role="status">
          Session persistence is not configured on the server. You can still ask
          questions, but this workspace will not be saved.
        </div>
      )}
      {missing && (
        <div className="session-banner" role="alert">
          That research session no longer exists.{" "}
          <Link href="/">Start a new one</Link>
        </div>
      )}
      <TranscriptView messages={session.messages} loading={session.loading} />
      <Workspace
        sessionId={sessionId}
        onNewResearch={handleNewResearch}
        onAskComplete={() => void session.refreshRecent()}
        sessions={session.recent}
        activeSessionId={sessionId}
        sessionsUnavailable={session.unavailable}
        onOpenSession={handleOpenSession}
      />
    </>
  );
}