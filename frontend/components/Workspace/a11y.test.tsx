import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { AxeResults, Result, RunOptions } from "axe-core";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Workspace from "./Workspace";

/**
 * axe-core gate across every workspace state. A violation here is a release
 * blocker, not a warning — the suite asserts zero violations of any severity,
 * so a new ARIA mistake fails CI instead of shipping.
 */

const OPTIONS: RunOptions = {
  resultTypes: ["violations"],
  rules: {
    // jsdom has no layout engine, so contrast cannot be evaluated meaningfully.
    // Colour contrast is verified in the browser, not here.
    "color-contrast": { enabled: false },
  },
};

async function audit(): Promise<AxeResults> {
  // axe.run's context overload types as void; the element overload is the one
  // that returns results, so the call is made through that signature.
  return (axe as unknown as { run: (ctx: Element, o: RunOptions) => Promise<AxeResults> }).run(
    document.body,
    OPTIONS,
  );
}

function report(results: AxeResults): string {
  return results.violations
    .map((v: Result) => `${v.id} (${v.impact}): ${v.help}\n  → ${v.nodes.map((n) => n.target.join(" ")).join("\n  → ")}`)
    .join("\n");
}

function stubOk(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }),
    ),
  );
}

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("Workspace accessibility — axe-core", () => {
  it("empty state has no violations", async () => {
    render(<Workspace />);
    const results = await audit();
    expect(report(results)).toBe("");
    expect(results.violations).toHaveLength(0);
  });

  it("answer state has no violations", async () => {
    const user = userEvent.setup();
    const { fixtureHybrid } = await import("../../lib/api");
    stubOk(fixtureHybrid);

    render(<Workspace />);
    await user.type(screen.getByLabelText(/research question/i), "MSC therapy trend in Indonesia?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());

    const results = await audit();
    expect(report(results)).toBe("");
  });

  it("clarification state has no violations", async () => {
    const user = userEvent.setup();
    const { fixtureClarify } = await import("../../lib/api");
    stubOk(fixtureClarify);

    render(<Workspace />);
    await user.type(screen.getByLabelText(/research question/i), "Which Rahman collaborates with Bandung labs?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: /needs clarification/i })).toBeInTheDocument());

    const results = await audit();
    expect(report(results)).toBe("");
  });

  it("zero-state has no violations", async () => {
    const user = userEvent.setup();
    const { fixtureNotFound } = await import("../../lib/api");
    stubOk(fixtureNotFound);

    render(<Workspace />);
    await user.type(screen.getByLabelText(/research question/i), "Quantum-dot yields in deep-sea fisheries?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /no supporting evidence found/i })).toBeInTheDocument(),
    );

    const results = await audit();
    expect(report(results)).toBe("");
  });

  it("loading state has no violations", async () => {
    const user = userEvent.setup();
    // Never-resolving fetch keeps the pipeline readout mounted.
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise(() => {})));

    render(<Workspace />);
    await user.type(screen.getByLabelText(/research question/i), "MSC therapy trend in Indonesia?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));
    await waitFor(() => expect(screen.getByRole("status", { name: /retrieval in progress/i })).toBeInTheDocument());

    const results = await audit();
    expect(report(results)).toBe("");
  });

  it("detail views have no violations", async () => {
    const user = userEvent.setup();
    const { fixtureHybrid } = await import("../../lib/api");
    stubOk(fixtureHybrid);

    render(<Workspace />);
    await user.type(screen.getByLabelText(/research question/i), "MSC therapy trend in Indonesia?");
    await user.click(screen.getByRole("button", { name: /synthesize/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());

    // Paper detail view: click a bibliography title, the way a reader would.
    const sources = screen.getByRole("region", { name: /^sources/i });
    const titles = within(sources).getAllByRole("heading", { level: 3 });
    await user.click(titles[0]!.querySelector("button")!);
    // Detail views hide the rail and give the centre column the full measure.
    await waitFor(() => expect(screen.queryByRole("region", { name: /^sources/i })).not.toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 2, name: fixtureHybrid.sources[0]!.title })).toBeInTheDocument(),
    );

    const results = await audit();
    expect(report(results)).toBe("");
  });

  it("the 50-evidence worst case has no violations", async () => {
    window.history.replaceState({}, "", "/?lab=answer&data=huge");
    render(<Workspace />);
    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());

    const results = await audit();
    expect(report(results)).toBe("");
  });

  it("the adversarial dataset has no violations", async () => {
    window.history.replaceState({}, "", "/?lab=answer&data=worst");
    render(<Workspace />);
    await waitFor(() => expect(screen.getByRole("heading", { name: /grounded answer/i })).toBeInTheDocument());

    const results = await audit();
    expect(report(results)).toBe("");
  });
});