import { describe, expect, it } from "vitest";
import {
  doiHref,
  formatMs,
  formatValue,
  matchCitationToSource,
  plural,
  renderAnswerParts,
  shortId,
  splitLead,
  statusLabel,
} from "./format";
import { fixtureHybrid } from "./api";
import type { SourceItem } from "./api";

// A citation the matcher can resolve must also be splittable by
// renderAnswerParts, or it renders as inert text instead of a link.
describe("renderAnswerParts / matchCitationToSource agreement", () => {
  const sources: SourceItem[] = [
    { publication_id: "p1", title: "Dated Study", year: 2023, doi: "10.1/a", source_type: "sql" },
    { publication_id: "p2", title: "Undated Study", year: null, doi: "10.1/b", source_type: "vector" },
  ];

  it("splits and resolves a year-bearing citation", () => {
    const parts = renderAnswerParts("Growth is strong [Dated Study, 2023, 10.1/a].");
    expect(parts.map((p) => p.kind)).toEqual(["text", "cite", "text"]);
    const cite = parts[1]!.value;
    expect(matchCitationToSource(cite, sources)).toBe("p1");
  });

  it("splits and resolves an n.d. citation", () => {
    const parts = renderAnswerParts("Some claim [Undated Study, n.d., 10.1/b] holds.");
    const cite = parts.find((p) => p.kind === "cite");
    expect(cite).toBeDefined();
    expect(matchCitationToSource(cite!.value, sources)).toBe("p2");
  });

  it("splits and resolves a no-doi citation by title and year", () => {
    const parts = renderAnswerParts("Claim [Dated Study, 2023, no-doi] here.");
    const cite = parts.find((p) => p.kind === "cite")!;
    expect(matchCitationToSource(cite.value, sources)).toBe("p1");
  });

  it("returns the whole string as text when there is no citation", () => {
    expect(renderAnswerParts("no citations here")).toEqual([{ kind: "text", value: "no citations here" }]);
  });

  it("keeps unmatched citations resolvable to null rather than throwing", () => {
    const parts = renderAnswerParts("Claim [Ghost Paper, 1999, 10.9/z] here.");
    const cite = parts.find((p) => p.kind === "cite")!;
    expect(matchCitationToSource(cite.value, sources)).toBeNull();
  });

  it("resolves a fixture citation against the fixture sources", () => {
    const cite = "[Mesenchymal Stem Cell Therapy for Cartilage Regeneration, 2023, 10.1016/j.cell.2023.01.002]";
    expect(matchCitationToSource(cite, fixtureHybrid.sources)).toBe("pub_89210");
  });
});

describe("formatValue", () => {
  it("renders null and undefined as an em dash", () => {
    expect(formatValue(null)).toBe("—");
    expect(formatValue(undefined)).toBe("—");
  });

  it("renders NaN and Infinity as an em dash", () => {
    expect(formatValue(NaN)).toBe("—");
    expect(formatValue(Infinity)).toBe("—");
  });

  it("passes strings through untouched", () => {
    expect(formatValue("citation_count")).toBe("citation_count");
  });

  it("formats integers with locale separators", () => {
    expect(formatValue(1284000)).toBe((1284000).toLocaleString());
  });

  it("renders sub-unit magnitudes as percentages", () => {
    expect(formatValue(0.284)).toBe("28.40%");
  });

  it("does not treat zero as a percentage", () => {
    expect(formatValue(0)).toBe("0");
  });
});

describe("formatMs", () => {
  it("renders non-numbers as an em dash", () => {
    // Number(null) === 0, so a bare isFinite would print a fake "0.0ms".
    expect(formatMs(null)).toBe("—");
    expect(formatMs(undefined)).toBe("—");
    expect(formatMs("12" as unknown as number)).toBe("—");
  });

  it("renders NaN and Infinity as an em dash", () => {
    expect(formatMs(NaN)).toBe("—");
    expect(formatMs(Infinity)).toBe("—");
  });

  it("formats finite values", () => {
    expect(formatMs(12.34)).toBe("12.3ms");
    expect(formatMs(12.34, 0)).toBe("12ms");
  });
});

describe("doiHref", () => {
  it("returns null for missing, blank, or sentinel DOIs", () => {
    expect(doiHref(undefined)).toBeNull();
    expect(doiHref(null)).toBeNull();
    expect(doiHref("")).toBeNull();
    expect(doiHref("   ")).toBeNull();
    expect(doiHref("no-doi")).toBeNull();
  });

  it("prefixes bare DOIs with the resolver", () => {
    expect(doiHref("10.1016/j.cell.2023.01.002")).toBe("https://doi.org/10.1016/j.cell.2023.01.002");
  });

  it("passes absolute URLs through", () => {
    expect(doiHref("https://doi.org/10.1/a")).toBe("https://doi.org/10.1/a");
  });

  it("trims surrounding whitespace", () => {
    expect(doiHref("  10.1/a  ")).toBe("https://doi.org/10.1/a");
  });
});

describe("plural", () => {
  it("uses Intl.PluralRules for the suffix", () => {
    expect(plural(1, "pub", "pubs")).toContain(" pub");
    expect(plural(3, "pub", "pubs")).toContain(" pubs");
  });

  it("never prints NaN", () => {
    expect(plural(NaN, "pub", "pubs")).toBe("— pubs");
  });
});

describe("shortId", () => {
  it("takes the first 8 characters", () => {
    expect(shortId("c83b7e41-6a20")).toBe("c83b7e41");
  });

  it("is safe against missing ids", () => {
    expect(shortId(null)).toBe("—");
    expect(shortId(undefined)).toBe("—");
    expect(shortId("")).toBe("—");
  });
});

describe("splitLead", () => {
  it("splits the first sentence into a lead", () => {
    expect(splitLead("First claim. Supporting detail.")).toEqual(["First claim.", "Supporting detail."]);
  });

  it("returns the whole string when there is no sentence break", () => {
    expect(splitLead("No terminator")).toEqual(["No terminator", ""]);
  });

  it("does not treat an over-long first sentence as a lead", () => {
    const long = `${"x".repeat(230)}.`;
    expect(splitLead(long)).toEqual([long, ""]);
  });
});

describe("statusLabel", () => {
  it("labels a live ok response", () => {
    expect(statusLabel(fixtureHybrid, true)).toBe("Verified against live database");
  });

  it("labels a snapshot ok response", () => {
    expect(statusLabel(fixtureHybrid, false)).toBe("Verified against prototype snapshot");
  });

  it("surfaces a non-ok status verbatim", () => {
    expect(statusLabel({ ...fixtureHybrid, status: "not_found" }, false)).toBe("not found");
  });
});