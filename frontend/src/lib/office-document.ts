import { suffixOf } from "@/lib/file-kinds";

/**
 * Office documents, read in the browser.
 *
 * Three formats, three readers, and one answer: a list of pages as markup, plus the
 * size they were laid out at. What the viewer does with them - a rail of thumbnails,
 * one page on a stage, a grid in a transcript - is the viewer's business, which is
 * why nothing here knows what a thumbnail is.
 *
 * **In the browser rather than on the server**, because the file is already in hand:
 * a workspace file arrives as bytes through an authenticated route, and asking a
 * second time for a rendering of it is a round trip a reader waits through. It also
 * keeps the API's rule intact - the byte routes serve a short allowlist inline and
 * everything else as a download, and nothing here widens that.
 *
 * The three readers are imported where they are used and never at module scope. They
 * are large (`pptx-preview` alone brings a charting library with it) and a viewer
 * that opens a `.png` must not pay for them, so each one arrives when a document of
 * its own kind is opened.
 */

/** What kind of document this is, at the granularity a reader has for it. */
export type OfficeFormat = "deck" | "doc" | "sheet";

/**
 * The suffixes each reader actually reads.
 *
 * `.doc`, `.ppt` and `.odt`/`.odp` are absent on purpose: they are the binary
 * formats from before OOXML, no reader here opens one, and offering a preview that
 * fails is worse than the download that works. `.xls` and `.ods` stay because
 * SheetJS reads them both.
 */
const BY_SUFFIX: Record<string, OfficeFormat> = {
  pptx: "deck",
  ppsx: "deck",
  docx: "doc",
  xlsx: "sheet",
  xlsm: "sheet",
  xls: "sheet",
  ods: "sheet",
};

/** The format a name says this file is, or null when nothing here reads it. */
export function officeFormatOf(name: string): OfficeFormat | null {
  return BY_SUFFIX[suffixOf(name)] ?? null;
}

/**
 * The size a deck is laid out at before it is scaled to fit.
 *
 * 16:9 at 960 is 540, which is the shape a deck is authored in and the shape the
 * viewer's stage wants; a deck that is not 16:9 keeps its own height, measured off
 * the rendered page rather than assumed here.
 */
const DECK_WIDTH = 960;
const DECK_HEIGHT = 540;

/** One document, rendered: its pages as markup, and the size they were laid out at. */
export interface RenderedDocument {
  /**
   * One HTML string per page, sanitised - see :func:`sanitise`.
   *
   * Markup rather than live nodes because the same page is drawn twice at two
   * scales: once on the stage and once in the rail. A string is copied by React
   * without either copy stealing the other's children, which is what happens when
   * one node is appended to a second parent.
   */
  pages: string[];
  /** The width every page is laid out at, in CSS pixels, before scaling. */
  width: number;
  /** The height every page is laid out at, in CSS pixels, before scaling. */
  height: number;
  /** Releases what rendering attached to the document - the reader's own styles. */
  dispose: () => void;
}

/**
 * A file's pages, rendered.
 *
 * Rejects where the file cannot be read - a corrupt archive, a format the suffix
 * promised and the bytes are not - and the caller answers that with a download rather
 * than an empty stage.
 */
export async function renderOfficePages(
  format: Exclude<OfficeFormat, "sheet">,
  file: Blob,
): Promise<RenderedDocument> {
  return format === "deck" ? renderDeck(file) : renderDoc(file);
}

/** A deck: one absolutely-positioned slide per page, as `pptx-preview` draws them. */
async function renderDeck(file: Blob): Promise<RenderedDocument> {
  const staging = stagingHost(DECK_WIDTH, DECK_HEIGHT);
  const { init } = await import("pptx-preview");
  const viewer = init(staging, {
    width: DECK_WIDTH,
    height: DECK_HEIGHT,
    // `list` rather than `slide`: every page is drawn at once, which is what a rail
    // of thumbnails is asking for. `slide` keeps one and swaps it.
    mode: "list",
  });

  try {
    await viewer.preview(await file.arrayBuffer());
    const slides = Array.from(staging.querySelectorAll<HTMLElement>(".pptx-preview-slide-wrapper"));
    if (slides.length === 0) throw new Error("no slides");
    // Measured before the nodes are serialised, and off the node itself: a 4:3 deck
    // is 720 tall where a 16:9 one is 540, and the stage has to know which it got.
    const width = slides[0]!.offsetWidth || DECK_WIDTH;
    const height = slides[0]!.offsetHeight || DECK_HEIGHT;
    return {
      pages: slides.map(sanitise),
      width,
      height,
      dispose: () => viewer.destroy?.(),
    };
  } finally {
    staging.remove();
  }
}

/** A document: one `section.docx` per page, as `docx-preview` breaks it. */
async function renderDoc(file: Blob): Promise<RenderedDocument> {
  const staging = stagingHost(900, 1200);
  // The reader's stylesheet goes on the body and stays there: the pages are markup
  // strings drawn elsewhere, and the classes they carry resolve against it.
  const styles = document.createElement("div");
  styles.dataset.officeStyles = "";
  document.body.append(styles);
  const dispose = () => styles.remove();

  try {
    const { renderAsync } = await import("docx-preview");
    await renderAsync(file, staging, styles, {
      className: "docx",
      inWrapper: true,
      // Pages, not one long column: the rail counts them, so they have to exist.
      breakPages: true,
      ignoreLastRenderedPageBreak: false,
      renderHeaders: true,
      renderFooters: true,
      renderFootnotes: true,
      renderEndnotes: true,
      // Data URLs rather than blob ones, so a page copied into the rail and one on
      // the stage both point at an address that outlives the node they came from.
      useBase64URL: true,
      experimental: true,
    });

    const sections = Array.from(staging.querySelectorAll<HTMLElement>("section.docx"));
    // A document with no page break in it still has a page: the reader's own wrapper
    // is the one it drew.
    const pages =
      sections.length > 0 ? sections : [staging.querySelector<HTMLElement>(".docx-wrapper")];
    const drawn = pages.filter((page): page is HTMLElement => page !== null);
    if (drawn.length === 0) throw new Error("no pages");
    const width = drawn[0]!.offsetWidth || 900;
    const height = drawn[0]!.offsetHeight || 1200;
    return { pages: drawn.map(sanitise), width, height, dispose };
  } catch (failure) {
    dispose();
    throw failure;
  } finally {
    staging.remove();
  }
}

/** One sheet, as the grid a reader expects. */
export interface SheetTable {
  name: string;
  /** The cells that will be drawn, as strings: what the file says, formatted. */
  rows: string[][];
  /** How many rows the sheet holds, so the grid can say what it left out. */
  rowsTotal: number;
}

/** Rows and columns drawn at most. Past this a table is a download, not a preview. */
const MAX_ROWS = 300;
const MAX_COLUMNS = 40;

/** A workbook's sheets, in the order the file lists them. */
export async function readWorkbook(file: Blob): Promise<SheetTable[]> {
  const { read, utils } = await import("xlsx");
  const workbook = read(new Uint8Array(await file.arrayBuffer()), { type: "array" });
  return workbook.SheetNames.map((name) => {
    const sheet = workbook.Sheets[name];
    const matrix = sheet
      ? (utils.sheet_to_json(sheet, { header: 1, raw: false, defval: "" }) as unknown[][])
      : [];
    const rows = matrix.slice(0, MAX_ROWS).map((row) =>
      Array.from({ length: Math.min(row.length, MAX_COLUMNS) }, (_, column) => {
        const cell = row[column];
        return cell === null || cell === undefined ? "" : String(cell);
      }),
    );
    return { name, rows, rowsTotal: matrix.length };
  });
}

/**
 * A box off screen for a reader to draw into, because every one of them measures.
 *
 * `visibility: hidden` rather than `display: none`: a reader that lays out into a
 * box with no layout draws a nought-pixel page, and the size measured off it would
 * be a guess.
 */
function stagingHost(width: number, height: number): HTMLElement {
  const host = document.createElement("div");
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = `position:fixed;top:0;left:-20000px;width:${width}px;height:${height}px;visibility:hidden;pointer-events:none;`;
  document.body.append(host);
  return host;
}

/** Nodes a document has no business carrying into this page. */
const DROPPED = new Set([
  "script",
  "iframe",
  "object",
  "embed",
  "link",
  "meta",
  "base",
  "form",
  "style",
]);

/** Attributes that name something to fetch or navigate to. */
const URL_ATTRIBUTES = new Set(["href", "src", "srcset", "poster", "action", "formaction"]);

/** Schemes a document may point at. Anything else - `javascript:`, `vbscript:` - goes. */
const SAFE_SCHEME = /^(?:https?:|data:image\/|data:font\/|blob:|#|\/|\.{0,2}\/)/i;

/**
 * A rendered page as markup, with everything that could run taken out of it.
 *
 * The bytes are a document an agent wrote or somebody uploaded, and "an agent wrote
 * it" is not a trust boundary: the same reasoning that keeps `.html` and `.svg` off
 * the API's inline allowlist applies here, because this page hands the markup to
 * `innerHTML`. `innerHTML` does not run a `<script>`, and it does run an `<img
 * onerror>` - so handlers, framing elements and script URLs are stripped before
 * anything is drawn.
 *
 * Inline `style` stays: it is how the reader positions every shape on a slide, and
 * it cannot execute. A nested `<style>` does not, because a stylesheet from the file
 * could restyle this dialog rather than its own page.
 */
function sanitise(node: HTMLElement): string {
  for (const element of [node, ...Array.from(node.querySelectorAll("*"))]) {
    if (DROPPED.has(element.tagName.toLowerCase())) {
      element.remove();
      continue;
    }
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on")) element.removeAttribute(attribute.name);
      else if (URL_ATTRIBUTES.has(name) && !SAFE_SCHEME.test(attribute.value.trim()))
        element.removeAttribute(attribute.name);
    }
  }
  return node.outerHTML;
}
