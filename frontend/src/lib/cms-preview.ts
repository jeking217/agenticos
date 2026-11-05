/**
 * What the preview pane loads, and how wide it is.
 *
 * The link itself comes from the server - it carries a signed ticket naming
 * this person and this site - so nothing here builds or guesses one. What this
 * module decides is the two things a browser cannot: how to make the frame
 * re-fetch after a save without disturbing the ticket, and how wide to draw it
 * when the person is checking the phone layout.
 *
 * Reloading is a query marker rather than a new link because the ticket is
 * short-lived and minting a fresh one on every save would make the pane blink
 * to a different URL for no visible reason. The proxy ignores query strings, so
 * `?_r=3` is exactly "same page, fetched again".
 */

export type PreviewDevice = "desktop" | "mobile";

/**: The width of a phone viewport, in CSS pixels - what the pane draws at. */
export const MOBILE_FRAME_WIDTH = 390;

export interface PreviewFrameSpec {
  src: string;
  device: PreviewDevice;
  /** `null` means "fill the pane". */
  width: number | null;
}

export function previewFrame(
  url: string | null,
  options: { device?: PreviewDevice; nonce?: number } = {},
): PreviewFrameSpec | null {
  if (!url) return null;
  const device = options.device ?? "desktop";
  const nonce = options.nonce ?? 0;
  return {
    src: nonce > 0 ? `${url}${url.includes("?") ? "&" : "?"}_r=${nonce}` : url,
    device,
    width: device === "mobile" ? MOBILE_FRAME_WIDTH : null,
  };
}
