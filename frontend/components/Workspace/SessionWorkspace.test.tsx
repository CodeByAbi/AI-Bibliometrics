import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Workspace from "./Workspace";
import { Sidebar } from "../Sidebar";
import { TranscriptView } from "./TranscriptView";
import { SessionWorkspace } from "./SessionWorkspace";
import { useRouter } from "next/navigation";
import type { SessionListItem } from "@/lib/sessions";

vi.mock("next/navigation", () => ({
  useRouter: vi.fn(),
  useParams: vi.fn(),
}));

/**
 * Session-aware UI behaviour.
 *
 * The properties under test are the ones a user would notice being wrong:
 *
 *  - New Research must create a PERSISTED session, not reset local state.
 *  - Opening a session must restore from the API without re-asking. The old
 *    sidebar re-issued a canned question, spending a retrieval to rebuild
 *    history the backend already had.
 *  - An ask inside a session must carry `session_id`, or the turn is not stored.
 *  - A 503 must read as "sessions are off", never as an empty history.
 *  - A failed turn must stay visible rather than silently vanishing.
 */

const SID = "11111111-1111-1111-1111-111111111111";
const SID2 = "22222222-2222-2222-2222-222222222222";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ASK_OK = {
  request_id: "r-1",
  status: "ok",
  route: "SQLRoute",
  answer: "14 publikasi.",
  evidence_objects: [],
  sources: [{ publication_id: "P1", title: "Paper A", source_type: "sql" }],
  filters_ignored: [],
  answered_via_fallback: false,
  unverified_citations: [],
};

const ASK_NOT_FOUND = {
  ...ASK_OK,
  request_id: "r-2",
  status: "not_found",
  answer: "Data tidak ditemukan dalam database.",
  sources: [],
};

/** Route fetches by URL so each test states only what it cares about. */
function routeFetch(routes: Record<string, () => Response>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    for (const [fragment, make] of Object.entries(routes)) {
      if (url.includes(fragment)) return make();
    }
    throw new Error(`unstubbed fetch: ${url}`);
  });
}

/** Read one fetch call as (url, init). Mirrors Workspace.test.tsx's pattern. */
function callOf(
  fetchMock: ReturnType<typeof vi.fn>,
  fragment: string,
  method?: string,
): [string, RequestInit] {
  const hit = fetchMock.mock.calls.find(([u, i]) => {
    const init = i as RequestInit | undefined;
    return (
      String(u).includes(fragment) && (method ? init?.method === method : true)
    );
  });
  if (!hit) throw new Error(`no fetch call matching ${fragment} ${method ?? ""}`);
  return [String(hit[0]), (hit[1] ?? {}) as RequestInit];
}

function bodyOf(fetchMock: ReturnType<typeof vi.fn>, fragment: string, method?: string) {
  return JSON.parse(String(callOf(fetchMock, fragment, method)[1].body));
}

function sessionRow(id: string, over: Partial<SessionListItem> = {}): SessionListItem {
  return {
    id,
    title: `Session ${id.slice(0, 4)}`,
    status: "active",
    created_at: "2026-01-02T03:04:05Z",
    updated_at: "2026-01-02T03:04:06Z",
    last_message_at: "2026-01-02T03:04:06Z",
    message_count: 2,
    source_count: 1,
    last_route: "SQLRoute",
    ...over,
  };
}

beforeEach(() => {
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("New Research", () => {
  it("creates a persisted session and routes to it", async () => {
    const user = userEvent.setup();
    const push = vi.fn();
    vi.mocked(useRouter).mockReturnValue({ push } as unknown as ReturnType<typeof useRouter>);

    const fetchMock = routeFetch({
      "/api/v1/sessions": () =>
        json(
          {
            id: SID,
            title: "New Research",
            status: "active",
            created_at: "2026-01-02T03:04:05Z",
            updated_at: "2026-01-02T03:04:05Z",
          },
          201,
        ),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SessionWorkspace sessionId={SID} />);

    await user.click(screen.getByRole("button", { name: /new research/i }));

    await waitFor(() => {
      expect(callOf(fetchMock, "/api/v1/sessions", "POST")).toBeTruthy();
    });
    // The new id becomes the workspace identity in the URL.
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/research/${SID}`));
  });

  it("Workspace delegates New Research rather than creating the session itself", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const onNewResearch = vi.fn();

    render(<Workspace onNewResearch={onNewResearch} />);
    await user.click(screen.getByRole("button", { name: /new research/i }));

    // Session creation belongs to SessionWorkspace, which owns routing. This
    // asserts the seam, not a duplicate POST from the shell.
    await waitFor(() => expect(onNewResearch).toHaveBeenCalled());
    const posts = fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "POST");
    expect(posts).toHaveLength(0);
  });

  it("clears the workspace even when session creation fails", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json({ request_id: "r", error: { error_type: "session_store_unavailable", message: "off", status_code: 503 } }, 503),
      ),
    );

    const onNewResearch = vi.fn();
    render(<Workspace onNewResearch={onNewResearch} />);

    await user.click(screen.getByRole("button", { name: /new research/i }));

    // The reset must not depend on the session call succeeding, or a 503 leaves
    // the previous answer under a button that appears to do nothing.
    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    });
    expect(onNewResearch).toHaveBeenCalled();
  });

  it("stays a local reset on the stateless route (no session id)", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<Workspace />);
    await user.click(screen.getByRole("button", { name: /new research/i }));

    // No POST: `/` is stateless by design, so a shared link never depends on
    // session persistence being configured.
    const posts = fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "POST");
    expect(posts).toHaveLength(0);
  });
});

describe("ask inside a session", () => {
  it("includes session_id in the request body", async () => {
    const user = userEvent.setup();
    const fetchMock = routeFetch({ "/api/v1/ask": () => json(ASK_OK) });
    vi.stubGlobal("fetch", fetchMock);

    render(<Workspace sessionId={SID} />);

    await user.type(screen.getByLabelText(/research question/i), "Berapa publikasi UI tahun 2023?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));

    await waitFor(() => {
      expect(bodyOf(fetchMock, "/api/v1/ask").session_id).toBe(SID);
    });
  });

  it("omits session_id entirely when there is no session", async () => {
    const user = userEvent.setup();
    const fetchMock = routeFetch({ "/api/v1/ask": () => json(ASK_OK) });
    vi.stubGlobal("fetch", fetchMock);

    render(<Workspace />);
    await user.type(screen.getByLabelText(/research question/i), "Berapa publikasi UI tahun 2023?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));

    await waitFor(() => {
      // Omitted, not null: the backend reads a missing id as "stateless".
      expect("session_id" in bodyOf(fetchMock, "/api/v1/ask")).toBe(false);
    });
  });

  it("renders a not_found answer rather than substituting data", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", routeFetch({ "/api/v1/ask": () => json(ASK_NOT_FOUND) }));

    render(<Workspace sessionId={SID} />);
    await user.type(screen.getByLabelText(/research question/i), "Quantum-dot yields?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));

    await waitFor(() => {
      expect(screen.getByText(/tidak ditemukan dalam database/i)).toBeInTheDocument();
    });
  });
});

describe("Recent Sessions", () => {
  function renderSidebar(props: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
    return render(
      <Sidebar
        activeView="empty"
        live
        devMode={false}
        sideOpen={false}
        onNavigate={() => undefined}
        onNewResearch={() => undefined}
        onAsk={() => undefined}
        onDevToggle={() => undefined}
        onClose={() => undefined}
        {...props}
      />,
    );
  }

  it("renders sessions from the API with their real counts", () => {
    renderSidebar({
      sessions: [
        sessionRow(SID, { title: "MSC therapy trend" }),
        sessionRow(SID2, {
          title: "Fresh workspace",
          message_count: 0,
          source_count: 0,
          last_route: null,
        }),
      ],
      activeSessionId: SID,
    });

    expect(screen.getByText("MSC therapy trend")).toBeInTheDocument();
    expect(screen.getByText("1 source · SQLRoute")).toBeInTheDocument();
    // An unanswered session must not be given a fabricated route.
    expect(screen.getByText("no questions yet")).toBeInTheDocument();
  });

  it("uses the list length for the count, not a literal", () => {
    renderSidebar({ sessions: [sessionRow(SID), sessionRow(SID2)] });
    const label = screen.getByText(/recent sessions/i);
    expect(label).toHaveTextContent("2");
  });

  it("marks the active session for assistive tech, not colour alone", () => {
    renderSidebar({ sessions: [sessionRow(SID), sessionRow(SID2)], activeSessionId: SID2 });
    const current = screen.getAllByRole("button", { current: "page" });
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Session 2222");
  });

  it("says sessions are off instead of showing an empty history", () => {
    renderSidebar({ sessions: [], sessionsUnavailable: true });
    expect(screen.getByText(/persistence is off on the server/i)).toBeInTheDocument();
  });

  it("tells a first-time user how to start a session", () => {
    renderSidebar({ sessions: [] });
    expect(screen.getByText(/start one with new research/i)).toBeInTheDocument();
  });

  it("opens a session instead of re-asking its question", async () => {
    const user = userEvent.setup();
    const onOpenSession = vi.fn();
    const onAsk = vi.fn();
    renderSidebar({ sessions: [sessionRow(SID)], onOpenSession, onAsk });

    await user.click(screen.getByRole("button", { name: /session 1111/i }));
    expect(onOpenSession).toHaveBeenCalledWith(SID);
    // The old behaviour re-issued a canned question per entry.
    expect(onAsk).not.toHaveBeenCalled();
  });

  it("omits the section entirely when sessions are not supplied", () => {
    renderSidebar({});
    expect(screen.queryByText(/recent sessions/i)).not.toBeInTheDocument();
  });
});

describe("TranscriptView", () => {
  it("is a pure read of restored turns", () => {
    render(
      <TranscriptView
        loading={false}
        messages={[
          { id: "m1", role: "user", content: "Berapa publikasi UI 2023?", status: "complete", created_at: "2026-01-02T03:04:05Z" },
          {
            id: "m2",
            role: "assistant",
            content: "14 publikasi.",
            status: "complete",
            created_at: "2026-01-02T03:04:06Z",
            request_id: "abcdef12-3456",
            route: "SQLRoute",
            evidence_objects: [],
            sources: [{ publication_id: "P1", title: "Paper A", source_type: "sql" }],
          },
        ]}
      />,
    );

    expect(screen.getByText("Berapa publikasi UI 2023?")).toBeInTheDocument();
    expect(screen.getByText("14 publikasi.")).toBeInTheDocument();
    expect(screen.getByText("Paper A")).toBeInTheDocument();
  });

  it("labels a restored answer as a snapshot and shows the re-verification key", () => {
    render(
      <TranscriptView
        loading={false}
        messages={[
          {
            id: "m2",
            role: "assistant",
            content: "14 publikasi.",
            status: "complete",
            created_at: "2026-01-02T03:04:06Z",
            request_id: "abcdef12-3456-7890",
            route: "SQLRoute",
          },
        ]}
      />,
    );
    // A stored number was true when produced; only re-asking re-verifies it.
    expect(screen.getByText(/snapshot of a past answer/i)).toBeInTheDocument();
    expect(screen.getByText(/abcdef12/i)).toBeInTheDocument();
  });

  it("keeps a failed turn visible instead of dropping it", () => {
    render(
      <TranscriptView
        loading={false}
        messages={[
          { id: "m1", role: "user", content: "q", status: "complete", created_at: "2026-01-02T03:04:05Z" },
          { id: "m2", role: "assistant", content: "no-answer-recorded", status: "failed", created_at: "2026-01-02T03:04:06Z" },
        ]}
      />,
    );
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText(/nothing was fabricated in its place/i)).toBeInTheDocument();
  });

  it("shows nothing for an empty conversation", () => {
    const { container } = render(<TranscriptView loading={false} messages={[]} />);
    expect(container.querySelector(".transcript")).toBeNull();
  });

  it("reports loading rather than rendering an empty transcript", () => {
    render(<TranscriptView loading messages={[]} />);
    const region = screen.getByRole("region", { name: /conversation history/i });
    expect(region).toHaveAttribute("aria-busy", "true");
  });
});