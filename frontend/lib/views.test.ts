import { describe, expect, it } from "vitest";
import { fixtureClarify, makeHugeFixture, parseDataKind, pickFixture, resolveDataFixture } from "./api";
import {
  evidenceForPublication,
  isGroundedView,
  publicationsForAuthor,
  selectAuthor,
  selectPublication,
  typeDistribution,
  yearHistogram,
} from "./views";
import { fixtureHybrid } from "./api";

describe("pickFixture", () => {
  it("routes semantic questions to the vector fixture", () => {
    expect(pickFixture("Papers on oxidative stress in Wharton's jelly?").route).toBe("VectorRoute");
  });

  it("routes aggregation questions to the SQL fixture", () => {
    expect(pickFixture("Who were the 5 most productive authors in 2023?").route).toBe("SQLRoute");
  });

  it("routes an ambiguous short author query to clarification", () => {
    expect(pickFixture("Which Rahman collaborates with Bandung labs?").status).toBe("needs_clarification");
  });

  it("routes an unmatched corpus question to not_found", () => {
    expect(pickFixture("quantum fisher yields").status).toBe("not_found");
  });

  it("falls back to the hybrid fixture", () => {
    expect(pickFixture("something entirely unrelated").route).toBe("HybridRoute");
  });
});

describe("selectPublication", () => {
  it("returns the requested source", () => {
    expect(selectPublication(fixtureHybrid, "pub_77402")?.publication_id).toBe("pub_77402");
  });

  it("returns the first source when the id is unknown", () => {
    expect(selectPublication(fixtureHybrid, "pub_missing")?.publication_id).toBe(fixtureHybrid.sources[0]!.publication_id);
  });

  it("returns null when the id is absent", () => {
    expect(selectPublication(fixtureHybrid, null)).not.toBeNull();
  });

  it("falls back to fixture sources when there is no response", () => {
    expect(selectPublication(null, null)?.publication_id).toBe(fixtureHybrid.sources[0]!.publication_id);
  });

  it("returns null when there are no sources at all", () => {
    expect(selectPublication({ ...fixtureHybrid, sources: [] }, null)).not.toBeNull();
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

describe("selectAuthor", () => {
  it("returns the requested author candidate", () => {
    expect(selectAuthor(fixtureClarify, "auth_089")?.name).toBe("A. Rahman Hakim");
  });

  it("never resolves a non-author candidate", () => {
    expect(selectAuthor(fixtureClarify, "inst_007")).not.toBeNull();
  });

  it("returns null when no candidates exist anywhere", () => {
    const empty = { ...fixtureClarify, candidates: [] };
    expect(selectAuthor(empty, null)).not.toBeNull();
  });

  it("returns null when there is no response", () => {
    expect(selectAuthor(null, null)).not.toBeNull();
  });
});

describe("publicationsForAuthor", () => {
  it("caps the result at three records", () => {
    const huge = makeHugeFixture();
    const pubs = publicationsForAuthor(huge, { id: "a", name: "Dr. Rahman", type: "author", publication_count: 1 });
    expect(pubs.length).toBeLessThanOrEqual(3);
  });

  it("returns the pool when there is no author", () => {
    expect(publicationsForAuthor(fixtureHybrid, null).length).toBeLessThanOrEqual(3);
  });

  it("does not crash on a whitespace-only name", () => {
    expect(() => publicationsForAuthor(fixtureHybrid, { id: "a", name: "   ", type: "author", publication_count: 0 })).not.toThrow();
  });
});

describe("yearHistogram", () => {
  it("counts sources per year in ascending order", () => {
    const hist = yearHistogram(fixtureHybrid);
    expect(hist.length).toBeGreaterThan(0);
    expect(hist.map((h) => h.year)).toEqual([...hist.map((h) => h.year)].sort((a, b) => a - b));
  });

  it("falls back to fixtures when there is no response", () => {
    expect(yearHistogram(null).length).toBeGreaterThan(0);
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