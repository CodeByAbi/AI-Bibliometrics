import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SourceItem } from "../../lib/api";
import { SourceCard } from "./SourceCard";

function source(overrides: Partial<SourceItem> = {}): SourceItem {
  return {
    publication_id: "pub_89210",
    title: "Mesenchymal Stem Cell Therapy for Cartilage Regeneration",
    year: 2023,
    doi: "10.1016/j.cell.2023.01.002",
    source_type: "analytics",
    relevance_score: 0.94,
    provenance: "Gold Layer: topics & researcher_expertise",
    ...overrides,
  };
}

function setup(overrides: Partial<React.ComponentProps<typeof SourceCard>> = {}) {
  const onOpen = vi.fn();
  const onCopy = vi.fn();
  const onEvidence = vi.fn();
  render(
    <SourceCard
      source={source()}
      highlighted={false}
      copied={false}
      animateDelay={0}
      onOpen={onOpen}
      onCopy={onCopy}
      onEvidence={onEvidence}
      {...overrides}
    />,
  );
  return { onOpen, onCopy, onEvidence };
}

describe("SourceCard", () => {
  it("exposes the title as a single button, not a clickable wrapper", async () => {
    const user = userEvent.setup();
    const { onOpen } = setup();
    const title = screen.getByRole("heading", { level: 3 }).querySelector("button")!;
    await user.click(title);
    expect(onOpen).toHaveBeenCalledWith("pub_89210");
    // No nested interactive descendants inside the title control.
    expect(title.querySelector("button, a")).toBeNull();
    expect(title.tagName).toBe("BUTTON");
  });

  it("publishes relevance as a progressbar with a text value", () => {
    setup({ source: source({ relevance_score: 0.94 }) });
    const bar = screen.getByRole("progressbar", { name: /relevance to the question/i });
    expect(bar).toHaveAttribute("aria-valuenow", "94");
    expect(bar).toHaveAttribute("aria-valuetext", "0.94 of 1.00");
    expect(screen.getByText("0.94")).toBeInTheDocument();
  });

  it("omits the relevance meter entirely when the score is null", () => {
    setup({ source: source({ relevance_score: null }) });
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("copies the DOI link and confirms in place", async () => {
    const user = userEvent.setup();
    const { onCopy } = setup();
    await user.click(screen.getByRole("button", { name: /^DOI — copy DOI link for/i }));
    expect(onCopy).toHaveBeenCalledWith("10.1016/j.cell.2023.01.002");
  });

  it("falls back to publication id when the record has no DOI", async () => {
    const user = userEvent.setup();
    const { onCopy } = setup({ source: source({ doi: null }) });
    expect(screen.getByText("no-doi")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^ID — copy publication ID for/i }));
    expect(onCopy).toHaveBeenCalledWith("pub_89210");
  });

  it("confirms the copy state in text, not only with colour", () => {
    setup({ copied: true });
    const button = screen.getByRole("button", { name: /copied DOI link/i });
    expect(button).toHaveClass("copy-ok");
    expect(button.textContent).toContain("Copied");
  });

  it("offers the DOI resolver in a new tab with a descriptive name", () => {
    setup();
    const link = screen.getByRole("link", { name: /open .* in a new tab/i });
    expect(link).toHaveAttribute("href", "https://doi.org/10.1016/j.cell.2023.01.002");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("highlights the evidence traced to this source", async () => {
    const user = userEvent.setup();
    const { onEvidence } = setup();
    await user.click(screen.getByRole("button", { name: /^Evidence traced to/i }));
    expect(onEvidence).toHaveBeenCalledWith("pub_89210");
  });

  it("renders a 255-character DOI as visible text on the resolver link", () => {
    const longDoi = `10.1016/j.${"supplemental-material-section-".repeat(7)}001`;
    setup({ source: source({ doi: longDoi }) });
    const doiLink = screen.getByRole("link", { name: /DOI:10\.1016/ });
    expect(doiLink).toHaveTextContent(longDoi);
    expect(doiLink.getAttribute("href")).toBe(`https://doi.org/${longDoi}`);
  });

  it("omits the resolver link when the record has no DOI", () => {
    setup({ source: source({ doi: null }) });
    expect(screen.queryByRole("link", { name: /in a new tab/i })).not.toBeInTheDocument();
  });
});