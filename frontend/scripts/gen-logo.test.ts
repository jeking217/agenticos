import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  ANIMATED_SVG,
  APP_ICON_SVG,
  buildAppIcon,
  buildLogo,
  buildModule,
  LOGO_SVG,
  LOGO_TS,
} from "./gen-logo";

/**
 * That the mark on disk is still the one the recipe draws.
 *
 * Derived files drift silently: the docs header, the README and the console all
 * still render, just a different face from the one beside them. So the files are
 * regenerated in memory and compared - `bun run gen:logo` rewrites them.
 */
describe("gen-logo", () => {
  it("keeps the docs and README mark in step with the recipe", () => {
    expect(readFileSync(LOGO_SVG, "utf8")).toBe(buildLogo());
  });

  it("keeps the console's data URI the same drawing as the file", () => {
    expect(readFileSync(LOGO_TS, "utf8")).toBe(buildModule(buildLogo()));
  });

  it("keeps the desktop icon's source the same drawing, on the dock's grid", () => {
    const icon = readFileSync(APP_ICON_SVG, "utf8");
    expect(icon).toBe(buildAppIcon(buildLogo()));
    expect(icon).toContain('viewBox="0 0 1024 1024"');
    expect(icon).toContain("scale(7.923076923076923)");
    expect(icon).not.toContain("<title>");
  });

  it("answers a still mark where the moving one used to live", () => {
    // The animated blob retired with the recipe that drew it; the file stays so
    // links written against it keep resolving, holding the same drawing.
    const animated = readFileSync(ANIMATED_SVG, "utf8");
    expect(animated).toBe(buildLogo());
    expect(animated).toContain("<image href=");
    expect(animated).not.toContain("animation");
  });

  it("draws a square so it fits every icon slot without letterboxing", () => {
    expect(buildLogo()).toContain('viewBox="0 0 104 104"');
  });

  it("carries the artwork as a JPEG data URI, not a redrawn path", () => {
    // The Unione Cloud artwork is a raster; the recipe wraps it verbatim rather
    // than tracing it, so the mark on disk is the mark the brand ships.
    const uri = /href="([^"]+)"/.exec(buildLogo())?.[1];
    expect(uri).toMatch(/^data:image\/jpeg;base64,/);
    expect(buildModule(buildLogo())).toContain(
      'export const LOGO_DATA_URI =\n  "data:image/jpeg;base64,',
    );
  });

  it("versions the module so a browser holding the old favicon asks again", () => {
    expect(buildModule(buildLogo())).toMatch(/export const LOGO_VERSION = "[0-9a-f]{8}";/);
  });
});
