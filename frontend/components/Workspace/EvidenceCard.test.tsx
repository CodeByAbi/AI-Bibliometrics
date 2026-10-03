import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { EvidenceObject } from "../../lib/api";
import { EvidenceCard } from "./EvidenceCard";

function evidence(overrides: Partial<EvidenceObject> = {}): EvidenceObject {
  return {
    claim: "MSC therapy publications grew 28.4% YoY in 2023",
    metric: "growth_score",
    value: 0.284,
    period: "2023",
    sources: [{ publication_id: "pub_89210", title: "Cartilage Regeneration", year: 2023 }],
    confidence: 1,
    ...overrides,
  };
}

function setup(overrides: Partial<React.ComponentProps<typeof EvidenceCard>> = {}) {
  const onToggle = vi.fn();
  const onSourceClick = vi.fn();
  render(
    <EvidenceCard
      ev={evidence()}
      index={0}
      expanded={false}
      highlighted={false}
      onToggle={onToggle}
      onSourceClick={onSourceClick}
      {...overrides}
    />,
  );
  return { onToggle, onSourceClick };
}

describe("EvidenceCard", () => {
  it("renders the claim, metric, value, and period from the evidence object", () => {
    setup();
    expect(screen.getByText("MSC therapy publications grew 28.4% YoY in 2023")).toBeInTheDocument();
    expect(screen.getByText("growth_score")).toBeInTheDocument();
    // 0.284 is a sub-unit ratio and must render as a percentage, not "0.28".
    expect(screen.getByText("28.40%")).toBeInTheDocument();
    expect(screen.getByText("2023")).toBeInTheDocument();
  });

  it("exposes the detail toggle as a real button wired to aria-expanded", async () => {
    const user = userEvent.setup();
    const { onToggle } = setup();
    const toggle = screen.getByRole("button", { name: /detail/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "ev-detail-0");

    await user.click(toggle);
    expect(onToggle).toHaveBeenCalledWith(0);
  });

  it("announces the confidence meter as a progressbar with a value", () => {
    setup({ ev: evidence({ confidence: 0.87 }) });
    const meter = screen.getByRole("progressbar", { name: /evidence confidence/i });
    expect(meter).toHaveAttribute("aria-valuenow", "87");
    expect(meter).toHaveAttribute("aria-valuetext", "87 percent");
  });

  it("does not print NaN when confidence is missing at runtime", () => {
    setup({ ev: evidence({ confidence: undefined as unknown as number }) });
    const meter = screen.getByRole("progressbar", { name: /evidence confidence/i });
    expect(meter).toHaveAttribute("aria-valuetext", "unknown");
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("links each source publication id to the bibliography", async () => {
    const user = userEvent.setup();
    const { onSourceClick } = setup();
    await user.click(screen.getByRole("button", { name: /show source pub_89210/i }));
    expect(onSourceClick).toHaveBeenCalledWith("pub_89210");
  });

  it("renders an em dash rather than a broken number for non-finite values", () => {
    setup({ ev: evidence({ value: Number.NaN }) });
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("keeps the card id stable so scroll targeting works", () => {
    const { container } = render(
      <EvidenceCard
        ev={evidence()}
        index={3}
        expanded={false}
        highlighted={false}
        onToggle={vi.fn()}
        onSourceClick={vi.fn()}
      />,
    );
    expect(container.querySelector("#ev-3")).not.toBeNull();
  });
});