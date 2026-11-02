import { describe, expect, it } from "vitest";

import { officeFormatOf } from "./office-document";

/**
 * Which reader opens which suffix.
 *
 * The three formats have three different readers, and one of them - the slide one -
 * is the only reason a deck is not a download. A suffix that falls through here is a
 * file the viewer offers to download, so the interesting cases are as much the ones
 * that stay out as the ones that come in.
 */
describe("officeFormatOf", () => {
  it("reads the three formats the product makes", () => {
    expect(officeFormatOf("deck.pptx")).toBe("deck");
    expect(officeFormatOf("minutes.docx")).toBe("doc");
    expect(officeFormatOf("data.xlsx")).toBe("sheet");
  });

  it("reads the variants the readers also open", () => {
    expect(officeFormatOf("slideshow.ppsx")).toBe("deck");
    expect(officeFormatOf("macro.xlsm")).toBe("sheet");
    expect(officeFormatOf("legacy.xls")).toBe("sheet");
    expect(officeFormatOf("open.ods")).toBe("sheet");
  });

  it("is not fooled by case, a path, or a name with dots in it", () => {
    expect(officeFormatOf("/workspace/Q3.REPORT.PPTX")).toBe("deck");
    expect(officeFormatOf("out/2026-Q3 纪要.docx")).toBe("doc");
  });

  it("leaves the formats nothing here reads to the download", () => {
    // The binary formats from before OOXML: a preview that fails is worse than the
    // download that works.
    expect(officeFormatOf("old.doc")).toBeNull();
    expect(officeFormatOf("old.ppt")).toBeNull();
    expect(officeFormatOf("report.pdf")).toBeNull();
    expect(officeFormatOf("data.csv")).toBeNull();
    expect(officeFormatOf("README")).toBeNull();
  });
});
