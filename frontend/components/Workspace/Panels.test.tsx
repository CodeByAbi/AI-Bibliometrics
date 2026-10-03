import { createRef } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fixtureClarify, fixtureNotFound } from "../../lib/api";
import { ClarifyPanel } from "./ClarifyPanel";
import { NotFoundPanel } from "./NotFoundPanel";

describe("ClarifyPanel", () => {
  it("lists every candidate as a toggle button inside a labelled group", () => {
    render(
      <ClarifyPanel
        response={fixtureClarify}
        selectedCand={null}
        onSelect={vi.fn()}
        onResolve={vi.fn()}
        onLoadExample={vi.fn()}
        titleRef={createRef<HTMLHeadingElement>()}
      />,
    );
    const group = screen.getByRole("group", { name: /candidate entities/i });
    const candidates = within(group).getAllByRole("button");
    expect(candidates).toHaveLength(fixtureClarify.candidates!.length);
    expect(candidates[0]).toHaveAttribute("aria-pressed", "false");
  });

  it("marks the selected candidate as pressed and disables resolve until then", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <ClarifyPanel
        response={fixtureClarify}
        selectedCand={null}
        onSelect={onSelect}
        onResolve={vi.fn()}
        onLoadExample={vi.fn()}
        titleRef={createRef<HTMLHeadingElement>()}
      />,
    );
    expect(screen.getByRole("button", { name: /resolve with selected entity/i })).toBeDisabled();
    expect(screen.getByText(/select a card to continue retrieval/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Dr\. A\. Rahman/i }));
    expect(onSelect).toHaveBeenCalledWith("auth_014");
  });

  it("replaces the p-tag candidate layout with phrasing content only", () => {
    render(
      <ClarifyPanel
        response={fixtureClarify}
        selectedCand="auth_014"
        onSelect={vi.fn()}
        onResolve={vi.fn()}
        onLoadExample={vi.fn()}
        titleRef={createRef<HTMLHeadingElement>()}
      />,
    );
    // Buttons may only contain phrasing content — no block-level children.
    const pressed = screen.getByRole("button", { name: /Dr\. A\. Rahman/i });
    expect(pressed.querySelector("p")).toBeNull();
    expect(pressed).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /resolve with selected entity/i })).toBeEnabled();
  });

  it("offers a corpus example when nothing is ambiguous", async () => {
    const user = userEvent.setup();
    const onLoadExample = vi.fn();
    render(
      <ClarifyPanel
        response={{ ...fixtureClarify, candidates: [] }}
        selectedCand={null}
        onSelect={vi.fn()}
        onResolve={vi.fn()}
        onLoadExample={onLoadExample}
        titleRef={createRef<HTMLHeadingElement>()}
      />,
    );
    expect(screen.getByRole("heading", { name: /no ambiguous entities/i })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /load corpus example/i }));
    expect(onLoadExample).toHaveBeenCalled();
  });
});

describe("NotFoundPanel", () => {
  it("explains what was searched and why retrieval stopped", () => {
    render(
      <NotFoundPanel
        response={fixtureNotFound}
        activeQuestion="Quantum-dot yields in deep-sea fisheries after 2020?"
        onTrySeed={vi.fn()}
        onLoadExample={vi.fn()}
        titleRef={createRef<HTMLHeadingElement>()}
      />,
    );
    expect(screen.getByRole("heading", { name: /no supporting evidence found/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /what was searched/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /why it stopped/i })).toBeInTheDocument();
    expect(screen.getByText(/short-circuit, no LLM call/i)).toBeInTheDocument();
  });

  it("keeps h2 → h3 heading order for the zero-state grid", () => {
    render(
      <NotFoundPanel
        response={fixtureNotFound}
        activeQuestion="q"
        onTrySeed={vi.fn()}
        onLoadExample={vi.fn()}
        titleRef={createRef<HTMLHeadingElement>()}
      />,
    );
    const title = screen.getByRole("heading", { level: 2, name: /no supporting evidence found/i });
    expect(title).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(2);
  });

  it("offers grounded retries instead of an invented answer", async () => {
    const user = userEvent.setup();
    const onTrySeed = vi.fn();
    render(
      <NotFoundPanel
        response={fixtureNotFound}
        activeQuestion="q"
        onTrySeed={onTrySeed}
        onLoadExample={vi.fn()}
        titleRef={createRef<HTMLHeadingElement>()}
      />,
    );
    await user.click(screen.getAllByRole("button", { name: /^Try:/i })[0]!);
    expect(onTrySeed).toHaveBeenCalledTimes(1);
  });

  it("distinguishes the grounded answer set from the zero-state example", () => {
    render(
      <NotFoundPanel
        response={{ ...fixtureNotFound, status: "ok" }}
        activeQuestion="q"
        onTrySeed={vi.fn()}
        onLoadExample={vi.fn()}
        titleRef={createRef<HTMLHeadingElement>()}
      />,
    );
    expect(screen.getByRole("heading", { name: /no zero-state in this answer set/i })).toBeInTheDocument();
  });
});