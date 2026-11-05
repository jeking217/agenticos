import { describe, expect, it } from "vitest";

import { MOBILE_FRAME_WIDTH, previewFrame } from "./cms-preview";

const TICKET_URL = "https://console.example.com/api/v1/cms/preview/abc.def/";

describe("previewFrame", () => {
  it("has nothing to show before a link exists", () => {
    expect(previewFrame(null)).toBeNull();
  });

  it("loads the link as the server signed it", () => {
    expect(previewFrame(TICKET_URL)?.src).toBe(TICKET_URL);
  });

  it("adds a reload marker only once something has been saved", () => {
    expect(previewFrame(TICKET_URL, { nonce: 0 })?.src).toBe(TICKET_URL);
    expect(previewFrame(TICKET_URL, { nonce: 3 })?.src).toBe(`${TICKET_URL}?_r=3`);
  });

  it("keeps a path that already carries a query string", () => {
    const framed = previewFrame("https://c.example.com/preview/t/about.html?x=1", { nonce: 2 });

    expect(framed?.src).toBe("https://c.example.com/preview/t/about.html?x=1&_r=2");
  });

  it("is full width on a desktop and phone width on a phone", () => {
    expect(previewFrame(TICKET_URL)?.width).toBeNull();
    expect(previewFrame(TICKET_URL, { device: "mobile" })?.width).toBe(MOBILE_FRAME_WIDTH);
  });
});
