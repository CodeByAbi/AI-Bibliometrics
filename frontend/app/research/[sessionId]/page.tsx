import { SessionWorkspace } from "@/components/Workspace/SessionWorkspace";
import { ErrorBoundary } from "@/components/ErrorBoundary";

/**
 * `/research/[sessionId]` — one research workspace, one session.
 *
 * The route is the canonical identity of the workspace: it survives a reload,
 * can be linked to, and agrees with what the backend persisted. Nothing here
 * reads the id from localStorage, because a second copy of "which session am I
 * in" is a second source of truth that drifts the moment a user opens a link or
 * presses Back.
 *
 * `SessionWorkspace` handles the not-found and not-a-UUID cases, so this page
 * stays a thin adapter from route params to the workspace.
 */
export default async function ResearchSessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  return (
    <ErrorBoundary>
      <SessionWorkspace sessionId={sessionId} />
    </ErrorBoundary>
  );
}