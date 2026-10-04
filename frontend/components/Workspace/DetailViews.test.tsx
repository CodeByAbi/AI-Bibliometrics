import { createRef } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fixtureHybrid, type AskResponse } from "../../lib/api";
import { ExploreView } from "../ExploreView";
import { PublicationDetailView } from "../PublicationDetailView";

const ref = () => createRef<HTMLHeadingElement>();
const src = fixtureHybrid.sources[0]!;

describe("PublicationDetailView", () => {
  it("restricts metadata to what the API returns — no invented abstract", () => {
    render(<PublicationDetailView publication={src} evidence={[]} onBack={vi.fn()} titleRef={ref()} />);
    expect(screen.getByRole("heading", { name: src.title, level: 2 })).toBeInTheDocument();
    expect(
      screen.getByText((_, el) => el?.tagName === "P" && !!el.textContent?.startsWith(src.publication_id)),
    ).toBeInTheDocument();
    // The empty-evidence copy explains absence rather than filling it in.
    expect(screen.getByText(/retrieved as context, not as the basis of a measured claim/i)).toBeInTheDocument();
  });

  it("uses h2, not h1, so the page keeps a single h1", () => {
    render(<PublicationDetailView publication={src} evidence={[]} onBack={vi.fn()} titleRef={ref()} />);
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("lists the evidence claims traced to the record", () => {
    const ev = fixtureHybrid.evidence_objects[0]!;
    render(<PublicationDetailView publication={src} evidence={[ev]} onBack={vi.fn()} titleRef={ref()} />);
    const list = screen.getByRole("list");
    expect(within(list).getByText(ev.claim)).toBeInTheDocument();
    expect(within(list).getByText(new RegExp(ev.metric))).toBeInTheDocument();
  });

  it("links the DOI to the resolver in a new tab", () => {
    render(<PublicationDetailView publication={src} evidence={[]} onBack={vi.fn()} titleRef={ref()} />);
    const link = screen.getByRole("link", { name: /^10\.1016\// });
    expect(link).toHaveAttribute("href", "https://doi.org/10.1016/j.cell.2023.01.002");
  });

  it("offers a recovery route when nothing is selected", async () => {
    const user = userEvent.setup();
    const onBack = vi.fn();
    render(<PublicationDetailView publication={null} evidence={[]} onBack={onBack} titleRef={ref()} />);
    expect(screen.getByRole("heading", { name: /no publication selected/i })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /return to brief/i }));
    expect(onBack).toHaveBeenCalled();
  });

  it("never prints NaN when relevance is missing at runtime", () => {
    render(
      <PublicationDetailView
        publication={{ ...src, relevance_score: null }}
        evidence={[]}
        onBack={vi.fn()}
        titleRef={ref()}
      />,
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});

describe("ExploreView", () => {
  const response: AskResponse = fixtureHybrid;

  it("charts the answer set and labels the plot for screen readers", () => {
    render(<ExploreView response={response} onOpenPublication={vi.fn()} titleRef={ref()} />);
    expect(screen.getByRole("heading", { name: /bibliometric trajectories/i, level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /publications per year/i })).toBeInTheDocument();
  });

  it("disables export when there is nothing to export", () => {
    const empty: AskResponse = { ...response, sources: [], evidence_objects: [] };
    render(<ExploreView response={empty} onOpenPublication={vi.fn()} titleRef={ref()} />);
    expect(screen.getByRole("button", { name: /export svg/i })).toBeDisabled();
    expect(screen.getByText(/no year-indexed sources/i)).toBeInTheDocument();
  });

  it("explains a single-year answer set instead of drawing a flat line", () => {
    const oneYear: AskResponse = { ...response, sources: [src] };
    render(<ExploreView response={oneYear} onOpenPublication={vi.fn()} titleRef={ref()} />);
    expect(screen.getByText(/single indexed year/i)).toBeInTheDocument();
  });

  it("shows evidence clusters as h3 under the section h2 — no skipped level", () => {
    render(<ExploreView response={response} onOpenPublication={vi.fn()} titleRef={ref()} />);
    const cluster = screen.getAllByRole("heading", { level: 3 })[0]!;
    expect(cluster).toHaveClass("explore-cluster-title");
    expect(screen.queryByRole("heading", { level: 4 })).not.toBeInTheDocument();
  });

  it("describes the source-type distribution for assistive technology", () => {
    render(<ExploreView response={response} onOpenPublication={vi.fn()} titleRef={ref()} />);
    const dist = screen.getByRole("img", { name: /source-type distribution/i });
    expect(dist).toHaveAccessibleName(/sql/);
  });
});