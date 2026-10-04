import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureHybrid, fixtureNotFound } from "../../lib/api";
import Workspace from "./Workspace";

/**
 * P0-A: a failed backend request must NEVER render bibliometric data.
 *
 * Before this fix, `use-ask.ts` accepted a `fallback: AskResponse` argument
 * (`pickFixture(question)`) and called `showResponse(fallback, false)` from its
 * catch block. Every 500, network failure and 8-second timeout therefore
 * rendered a fabricated brief — "Dr. A. Rahman published 14 papers", expertise
 * score 84.50, MSC growth 28.4% — behind a one-line "Showing a prototype
 * snapshot instead" note that a screenshot would not carry.
 *
 * Every test below asserts the ABSENCE of those strings. That is the contract:
 * a number on this page must have come from the backend.
 */

const QUESTION = "MSC therapy trend in Indonesia after 2020?";

/**
 * Strings that exist ONLY inside the dev fixtures. If any of these appear in
 * the document, fabricated bibliometric data is on screen.
 */
const FABRICATED = [
  "Dr. A. Rahman", // fixture author
  "Dr. B. Santoso", // fixture author
  "84.5", // fixture expertise_score
  "84.50",
  "28.4", // fixture growth_score
  "14 publications", // fixture publication_count
  "Mesenchymal Stem Cell Therapy for Cartilage Regeneration", // fixture title
  "Wharton's Jelly Isolation Protocol", // fixture title
  "pub_89210", // fixture publication id
  "pub_77402",
  "prototype snapshot", // the old disclaimer
];

function expectNoFabricatedData(container: HTMLElement = document.body) {
  const text = container.textContent ?? "";
  for (const needle of FABRICATED) {
    expect(text, `fabricated data "${needle}" must not be rendered`).not.toContain(needle);
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function askQuestion(user: ReturnType<typeof userEvent.setup>, question = QUESTION) {
  const box = screen.getByLabelText(/research question/i);
  await user.clear(box);
  await user.type(box, question);
  await user.click(screen.getByRole("button", { name: /synthesize/i }));
}

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
});

describe("P0-A: backend failure never renders fabricated data", () => {
  it("HTTP 500 renders an error state with no numbers", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            request_id: "r-500",
            error: { error_type: "internal_error", message: "boom", status_code: 500 },
          },
          500,
        ),
      ),
    );
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be completed/i);
    expectNoFabricatedData();
  });

  it("network failure renders an error state with no numbers", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expectNoFabricatedData();
  });

  it("timeout renders a TIMEOUT state, distinct from a generic error", async () => {
    const user = userEvent.setup();
    // Backend 504 llm_timeout — the P0-B bounded-generation contract value.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            request_id: "r-504",
            error: { error_type: "llm_timeout", message: "generator too slow", status_code: 504 },
          },
          504,
        ),
      ),
    );
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // A timeout says so. Collapsing it into "something went wrong" is exactly
    // what the P0 brief forbade.
    expect(screen.getByRole("alert")).toHaveTextContent(/timed out/i);
    expectNoFabricatedData();
  });

  it("malformed response body renders an error state with no numbers", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("{not json at all", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expectNoFabricatedData();
  });

  it("a 200 with a wrong-shaped body is rejected, not rendered", async () => {
    const user = userEvent.setup();
    // Missing `sources` and `evidence_objects`: previously cast to AskResponse
    // and trusted, so the views read `undefined.length` off it.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ request_id: "r-shape", status: "ok", route: "SQLRoute", answer: "hi" }),
      ),
    );
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expectNoFabricatedData();
  });

  it("states explicitly that no data is displayed", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // Without this, empty space reads as "zero results", which is a different
    // and wrong claim: the backend never got to answer.
    expect(screen.getByRole("alert")).toHaveTextContent(/no data is displayed/i);
  });

  it("offers a retry action on failure", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /retry query/i })).toBeEnabled();
  });

  it("a failed follow-up does not present the previous answer as the new one", async () => {
    const user = userEvent.setup();
    // First call succeeds with real data; second fails.
    const fetchMock = vi.fn();
    fetchMock
      .mockImplementationOnce(() => Promise.resolve(jsonResponse(fixtureHybrid)))
      .mockImplementationOnce(() =>
        Promise.resolve(
          jsonResponse(
            { request_id: "r-2", error: { error_type: "internal_error", message: "boom", status_code: 500 } },
            500,
          ),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<Workspace />);
    await askQuestion(user);
    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());

    await askQuestion(user, "Who were the 5 most productive authors in 2023?");
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());

    // The failure report itself must contain nothing but the failure.
    expectNoFabricatedData(screen.getByRole("alert"));

    // And the previous brief is gone rather than lingering under the new
    // question, where its numbers would read as question two's answer.
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: /grounded answer/i })).not.toBeInTheDocument(),
    );
  });
});

describe("P0-A: a valid empty result stays an empty result", () => {
  it("200 not_found renders the zero-state, not an error", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(fixtureNotFound)));

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("heading", { name: /no supporting evidence found/i })).toBeInTheDocument());
    // Crucially NOT an error: the backend answered correctly.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expectNoFabricatedData();
  });

  it("200 ok with zero sources renders the answer with empty rails", async () => {
    const user = userEvent.setup();
    const emptyOk = {
      ...fixtureHybrid,
      status: "ok" as const,
      answer: "No publication matched the filters for this window.",
      evidence_objects: [],
      sources: [],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(emptyOk)));

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());
    // The rail stays mounted and reports a count of ZERO. That is the point:
    // a valid empty answer set renders as empty, and does not borrow the
    // fixture's four sources to fill the space.
    const evidenceHead = screen.getByRole("heading", { name: /verified evidence/i });
    expect(evidenceHead).toHaveTextContent("0");
    const sourcesHead = screen.getByRole("heading", { name: /^sources/i });
    expect(sourcesHead).toHaveTextContent("0");
    // And it is not an error: the backend answered correctly.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expectNoFabricatedData();
  });

  it("not_found does not leak fixture data into the Trends view", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(fixtureNotFound)));

    render(<Workspace />);
    await askQuestion(user);
    await waitFor(() => expect(screen.getByRole("heading", { name: /no supporting evidence found/i })).toBeInTheDocument());

    // Navigate to Trends. The chart must report zero publications, not the
    // four the fixture invents.
    await user.click(screen.getByRole("button", { name: /trends/i }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /bibliometric trajectories/i })).toBeInTheDocument(),
    );
    expect(screen.getByText(/cross-analyzing 0 cited publications/i)).toBeInTheDocument();
    expectNoFabricatedData();
  });
});

describe("P0-A: real data still renders", () => {
  it("renders the backend's own numbers on success", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(fixtureHybrid)));

    render(<Workspace />);
    await askQuestion(user);

    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());
    // The SAME strings that must be absent on failure are correct here,
    // because the backend returned them.
    expect(document.body.textContent).toContain("Mesenchymal Stem Cell Therapy for Cartilage Regeneration");
  });
});

describe("P0-A: production build ignores the ?lab= fixture override", () => {
  it("does not force fixture data from the query string", async () => {
    vi.stubEnv("NODE_ENV", "production");
    try {
      window.history.replaceState({}, "", "/?lab=answer");
      render(<Workspace />);
      // The idle workspace renders — no fixture brief was injected.
      expect(screen.getByRole("heading", { level: 1, name: /research intelligence workspace/i })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: /grounded answer/i })).not.toBeInTheDocument();
      expectNoFabricatedData();
    } finally {
      vi.unstubAllEnvs();
    }
  });
});

describe("P0-A: fixture replay controls are dev-only", () => {
  it("hides the Replay strip in the default UI", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(fixtureHybrid)));

    render(<Workspace />);
    await askQuestion(user);
    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());

    expect(screen.queryByRole("group", { name: /replay a sample route/i })).not.toBeInTheDocument();
  });

  it("hides the Load-example buttons in the default UI", async () => {
    const user = userEvent.setup();
    const noCandidates = { ...fixtureHybrid, status: "needs_clarification" as const, candidates: [] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(noCandidates)));

    render(<Workspace />);
    await askQuestion(user);
    await waitFor(() => expect(screen.getByRole("heading", { name: /no ambiguous entities/i })).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: /load corpus example/i })).not.toBeInTheDocument();
  });
});