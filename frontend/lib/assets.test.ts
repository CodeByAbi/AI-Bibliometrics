import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const frontend = process.cwd();
const publicDir = join(frontend, "public");
const appDir = join(frontend, "app");

function md5(file: string): string {
  return createHash("md5").update(readFileSync(file)).digest("hex");
}

function walk(dir: string, depth = 0): string[] {
  if (depth > 3 || !existsSync(dir)) return [];
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? walk(full, depth + 1) : [full];
  });
}

function pngDimensions(file: string): { width: number; height: number } {
  const buf = readFileSync(file);
  expect(buf.subarray(0, 8).toString("hex"), `${file} is not a PNG`).toBe("89504e470d0a1a0a");
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

describe("app icon assets", () => {
  it("ships public/favicon.ico as a real multi-image ICO", () => {
    const buf = readFileSync(join(publicDir, "favicon.ico"));
    expect(buf.readUInt16LE(0), "ICO reserved field must be 0").toBe(0);
    expect(buf.readUInt16LE(2), "ICO type field must be 1 (icon)").toBe(1);
    expect(buf.readUInt16LE(4), "ICO must declare at least one image").toBeGreaterThan(0);
  });

  it("keeps every icon referenced by layout metadata backed by a real file", () => {
    const layout = readFileSync(join(appDir, "layout.tsx"), "utf8");
    const referenced = Array.from(layout.matchAll(/"(\/[^"]+\.(?:ico|png|svg|jpe?g|gif|webp))"/g)).map((m) => m[1]);
    expect(referenced.length, "layout.tsx should declare at least one icon").toBeGreaterThan(0);
    for (const url of referenced) {
      expect(existsSync(join(publicDir, url)), `${url} is declared in layout.tsx but missing from public/`).toBe(true);
    }
  });

  it("has no byte-identical asset in both app/ and public/", () => {
    const publicAssets = walk(publicDir).filter((f) => /\.(ico|png|svg|jpe?g|gif|webp)$/i.test(f));
    const appAssets = walk(appDir).filter((f) => /\.(ico|png|svg|jpe?g|gif|webp)$/i.test(f));
    const publicHashes = new Map(publicAssets.map((f) => [md5(f), f]));
    for (const asset of appAssets) {
      const twin = publicHashes.get(md5(asset));
      expect(twin, `${asset} duplicates ${twin} — the app/ copy shadows public/ and doubles the payload`).toBeUndefined();
    }
  });

  it("keeps logo.png sized for its render slot, not its source canvas", () => {
    const css = readFileSync(join(appDir, "globals.css"), "utf8");
    const slot = Number(css.match(/\.topbar-logo\s*\{[^}]*width:\s*(\d+)px/)?.[1] ?? 28);
    expect(slot).toBeGreaterThan(0);

    const logo = join(publicDir, "logo.png");
    const { width, height } = pngDimensions(logo);
    const budget = slot * 4;

    expect(width, `logo.png is ${width}px wide for a ${slot}px slot`).toBeLessThanOrEqual(budget);
    expect(height, `logo.png is ${height}px tall for a ${slot}px slot`).toBeLessThanOrEqual(budget);
    expect(statSync(logo).size, "logo.png should stay small enough to preload cheaply").toBeLessThan(64 * 1024);
  });
});