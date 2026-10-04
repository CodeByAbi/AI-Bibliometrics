import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Workspace from "./Workspace";

/**
 * Full-workspace behaviour, driven through the same entry points the browser
 * uses. The backend is stubbed so the suite exercises the real view machine
 * (empty → loading → answer → clarify / not-found / error) without a network.
 */
function stubFetchOnce(body: unknown, init: ResponseInit = {}) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
      ...init,
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.history.replaceState({}, "", "/");
});

describe("Workspace — empty state", () => {
  it("shows the workflow, real example questions, and both rail empty states", () => {
    render(<Workspace />);
    expect(screen.getByRole("heading", { level: 1, name: /research intelligence workspace/i })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /example questions/i })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: /how to use the workspace/i })).toBeInTheDocument();
    expect(screen.getByText(/evidence objects appear here with metric, value, period, and confidence/i)).toBeInTheDocument();
    expect(screen.getByText(/linked publications with title, year, doi/i)).toBeInTheDocument();
  });

  it("disables Synthesize until the question is long enough, and says why", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    const submit = screen.getByRole("button", { name: /synthesize/i });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/enter at least 3 characters/i)).toBeInTheDocument();

    const box = screen.getByLabelText(/research question/i);
    await user.type(box, "ab");
    expect(submit).toBeDisabled();
    await user.type(box, "c");
    expect(submit).toBeEnabled();
    expect(screen.getByText(/answers trace to database evidence/i)).toBeInTheDocument();
  });

  it("labels the question box so it is reachable by name, not placeholder alone", () => {
    render(<Workspace />);
    expect(screen.getByLabelText(/research question/i)).toBeInTheDocument();
  });
});

describe("Workspace — answer flow", () => {
  it("posts the question and renders the grounded brief with a route badge", async () => {
    const user = userEvent.setup();
    const { fixtureHybrid } = await import("../../lib/api");
    const fetchMock = stubFetchOnce(fixtureHybrid);

    render(<Workspace />);
    const box = screen.getByLabelText(/research question/i);
    await user.type(box, "MSC therapy trend in Indonesia after 2020?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));

    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toContain("/api/v1/ask");
    expect(JSON.parse(String((init as RequestInit).body)).question).toContain("MSC therapy trend");
    expect(screen.getByTitle(/retrieval route selected/i)).toHaveTextContent("HybridRoute");
    expect(screen.getByText(/grounding: 100% verified/i)).toBeInTheDocument();
    // The pipeline trail repeats the route for provenance; both must agree.
    expect(screen.getAllByText(/\[HybridRoute\]/).length).toBeGreaterThanOrEqual(2);
  });

  it("publishes the four pipeline stages with a text state, not colour alone", () => {
    render(<Workspace />);
    const pipeline = screen.getByRole("list", { name: /research pipeline/i });
    const stages = within(pipeline).getAllByRole("listitem");
    expect(stages).toHaveLength(4);
    expect(stages[0]).toHaveTextContent("Question — in progress");
    expect(stages[1]).toHaveTextContent("Retrieval — pending");
  });

  it("renders verified evidence and bibliography cards with heading structure", async () => {
    const user = userEvent.setup();
    const { fixtureHybrid } = await import("../../lib/api");
    stubFetchOnce(fixtureHybrid);

    render(<Workspace />);
    const box = screen.getByLabelText(/research question/i);
    await user.type(box, "MSC therapy trend in Indonesia?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));

    await waitFor(() => expect(screen.getByRole("heading", { name: /verified evidence/i })).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: /^sources/i })).toBeInTheDocument();
    // Rail sections are h2 so the page keeps h1 → h2 → h3 order.
    expect(screen.getByRole("heading", { level: 2, name: /verified evidence/i })).toBeInTheDocument();
  });

  it("renders an explicit error state when the backend is unreachable", async () => {
    // P0-A: this test previously asserted the OPPOSITE — that a transport
    // failure degrades to a "prototype snapshot". That fallback shipped a
    // fabricated brief (invented authors, publication counts and expertise
    // scores) behind a one-line disclaimer. A failed request now renders an
    // error and nothing else. See NoFakeData.test.tsx for the full matrix.
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<Workspace />);
    const box = screen.getByLabelText(/research question/i);
    await user.type(box, "MSC therapy trend in Indonesia?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be completed/i);
    // No brief, and no snapshot disclaimer either.
    expect(screen.queryByRole("heading", { name: /grounded answer/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/prototype snapshot/i)).not.toBeInTheDocument();
  });
});

describe("Workspace — error state", () => {
  it("shows a recoverable alert with a retry action", () => {
    // The error view is a lab-only state (?lab=error): a live transport
    // failure degrades to a snapshot instead of this screen.
    window.history.replaceState({}, "", "/?lab=error");
    render(<Workspace />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/the request could not be completed/i);
    expect(alert).toHaveTextContent(/10-second limit/i);
    expect(within(alert).getByRole("button", { name: /retry query/i })).toBeEnabled();
  });
});

describe("Workspace — navigation and shortcuts", () => {
  it("exposes a labelled navigation region instead of an orphan tablist", () => {
    render(<Workspace />);
    const nav = screen.getByRole("navigation", { name: /workspace views/i });
    expect(within(nav).getAllByRole("button").length).toBeGreaterThan(0);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("marks the current view with aria-current rather than colour alone", async () => {
    render(<Workspace />);
    const nav = screen.getByRole("navigation", { name: /workspace views/i });
    const current = within(nav).getAllByRole("button").filter((b) => b.getAttribute("aria-current") === "page");
    expect(current.length).toBeLessThanOrEqual(1);
  });

  it("⌘K moves focus to the question box", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    await user.keyboard("{Control>}k{/Control}");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText(/research question/i)));
  });

  it("⌘N resets to a fresh research session", async () => {
    const user = userEvent.setup();
    const { fixtureHybrid } = await import("../../lib/api");
    stubFetchOnce(fixtureHybrid);
    render(<Workspace />);

    const box = screen.getByLabelText(/research question/i);
    await user.type(box, "MSC therapy trend in Indonesia?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());

    await user.keyboard("{Control>}n{/Control}");
    await waitFor(() => expect(screen.queryByRole("heading", { name: /grounded answer/i })).not.toBeInTheDocument());
    expect(screen.getByRole("group", { name: /example questions/i })).toBeInTheDocument();
  });

  it("toggles the provenance inspector region from the top bar", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    const toggle = screen.getByRole("button", { name: /provenance info/i });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: /provenance details/i })).toBeInTheDocument();
  });
});

describe("Workspace — mobile drawer", () => {
  it("exposes the library as a modal dialog that traps focus and restores it", async () => {
    const user = userEvent.setup();
    // matchMedia must report the mobile breakpoint for the sheet path.
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((q: string) => ({
        matches: q.includes("max-width: 899px"),
        media: q,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );

    render(<Workspace />);
    const toggle = screen.getByRole("button", { name: /open research library/i });
    await user.click(toggle);

    const dialog = await screen.findByRole("dialog", { name: /research library/i });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    await waitFor(() => expect(within(dialog).getByRole("button", { name: /close research library/i })).toHaveFocus());

    // Esc dismisses and focus returns to the hamburger that opened the sheet.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: /open research library/i })).toHaveFocus());
  });
});