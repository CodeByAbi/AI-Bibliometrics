import { describe, expect, it } from "vitest";
import { fixtureHybrid, makeHugeFixture, parseDataKind, resolveDataFixture } from "./api";
import {
  evidenceForPublication,
  isGroundedView,
  selectPublication,
  typeDistribution,
  yearHistogram,
} from "./views";

/**
 * P0-A: these tests previously asserted the OPPOSITE — that a null response
 * yields `fixtureHybrid` data. That behaviour rendered four invented
 * publications in the Trends chart and a fabricated Paper Detail page whenever
 * the backend had not answered (error, timeout) or had legitimately returned
 * zero sources (not_found).
 *
 * The contract they now lock in: no response means NO data, never a fixture.
 */
describe("no fixture data when there is no response (P0-A)", () => {
  it("selectPublication returns null with no response", () => {
    expect(selectPublication(null, null)).toBeNull();
    expect(selectPublication(null, "pub_89210")).toBeNull();
  });

  it("selectPublication returns null when the answer set retrieved zero sources", () => {
    // A legitimate not_found. Previously this borrowed the fixture's sources
    // and rendered a real-looking publication detail page.
    expect(selectPublication({ ...fixtureHybrid, sources: [] }, null)).toBeNull();
  });

  it("yearHistogram is empty with no response", () => {
    expect(yearHistogram(null)).toEqual([]);
  });

  it("typeDistribution is empty with no response", () => {
    expect(typeDistribution(null)).toEqual([]);
  });

  it("charts zero sources for an empty answer set rather than the fixture's", () => {
    const empty = { ...fixtureHybrid, sources: [] };
    expect(yearHistogram(empty)).toEqual([]);
    expect(typeDistribution(empty)).toEqual([]);
  });

  it("never surfaces a fixture publication_id from a null response", () => {
    for (const src of [selectPublication(null, null)]) {
      expect(src?.publication_id).not.toBe(fixtureHybrid.sources[0]!.publication_id);
    }
  });
});

describe("selectPublication", () => {
  it("returns the requested source", () => {
    expect(selectPublication(fixtureHybrid, "pub_77402")?.publication_id).toBe("pub_77402");
  });

  it("returns the first source when the id is unknown", () => {
    expect(selectPublication(fixtureHybrid, "pub_missing")?.publication_id).toBe(fixtureHybrid.sources[0]!.publication_id);
  });

  it("returns the first source when the id is absent", () => {
    expect(selectPublication(fixtureHybrid, null)).not.toBeNull();
  });
});

describe("evidenceForPublication", () => {
  it("traces evidence objects back to a publication", () => {
    const found = evidenceForPublication(fixtureHybrid, "pub_89210");
    expect(found.length).toBeGreaterThan(0);
    expect(found.every((ev) => ev.sources.some((s) => s.publication_id === "pub_89210"))).toBe(true);
  });

  it("returns an empty array for an unknown publication", () => {
    expect(evidenceForPublication(fixtureHybrid, "pub_missing")).toEqual([]);
  });

  it("returns an empty array when there is no response", () => {
    expect(evidenceForPublication(null, "pub_89210")).toEqual([]);
  });
});

describe("yearHistogram", () => {
  it("counts sources per year in ascending order", () => {
    const hist = yearHistogram(fixtureHybrid);
    expect(hist.length).toBeGreaterThan(0);
    expect(hist.map((h) => h.year)).toEqual([...hist.map((h) => h.year)].sort((a, b) => a - b));
  });
});

describe("typeDistribution", () => {
  it("produces shares that sum to one", () => {
    const total = typeDistribution(fixtureHybrid).reduce((a, d) => a + d.share, 0);
    expect(total).toBeCloseTo(1, 5);
  });

  it("never divides by zero", () => {
    const empty = { ...fixtureHybrid, sources: [] };
    expect(() => typeDistribution(empty)).not.toThrow();
  });
});

describe("isGroundedView", () => {
  it("accepts answer-bearing views", () => {
    expect(isGroundedView("answer")).toBe(true);
    expect(isGroundedView("publication")).toBe(true);
  });

  it("rejects transient and lab views", () => {
    expect(isGroundedView("loading")).toBe(false);
    expect(isGroundedView("empty")).toBe(false);
    expect(isGroundedView("error")).toBe(false);
  });
});

describe("resolveDataFixture / parseDataKind", () => {
  it("returns null for demo so normal behavior is preserved", () => {
    expect(resolveDataFixture("demo")).toBeNull();
  });

  it("returns a fixture for each adversarial dataset", () => {
    for (const kind of ["worst", "empty", "one", "huge"] as const) {
      expect(resolveDataFixture(kind)).not.toBeNull();
    }
  });

  it("builds the 50-record ceiling fixture deterministically", () => {
    const a = makeHugeFixture();
    const b = makeHugeFixture();
    expect(a.sources.length).toBe(50);
    expect(a.evidence_objects.length).toBe(50);
    expect(a.sources.map((s) => s.publication_id)).toEqual(b.sources.map((s) => s.publication_id));
  });

  it("parses only known dataset names", () => {
    expect(parseDataKind("worst")).toBe("worst");
    expect(parseDataKind("bogus")).toBe("demo");
    expect(parseDataKind(null)).toBe("demo");
  });
});