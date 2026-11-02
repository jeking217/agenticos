"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

interface PagedDocumentProps {
  /** One page per entry, as sanitised markup. */
  pages: string[];
  /** The size every page is laid out at, before scaling. */
  width: number;
  height: number;
  /** The page to open on, for a reader who clicked the fourth thumbnail elsewhere. */
  initialPage?: number;
  /**
   * What "fitted" means.
   *
   * `page` shows the whole page - a slide is read as a picture, and half of one is
   * not a preview. `width` fills the width and lets the page run past the bottom,
   * which is what a document wants: an A4 page fitted by height in a wide dialog is
   * a page of eight-point text.
   */
  fitMode?: "page" | "width";
}

/** The rail's own width, and the thumbnail inside it. */
const RAIL_WIDTH = 148;
const THUMB_WIDTH = RAIL_WIDTH - 32;

/** Room the stage keeps around the page, so a fit page is not flush with the edge. */
const STAGE_GUTTER = 48;

const ZOOM_STEP = 0.1;
const ZOOM_MIN = 0.25;
const ZOOM_MAX = 3;

/**
 * A document as pages: a rail of thumbnails down the left, the page itself beside it.
 *
 * **Two scrolls, not one.** The deck used to be a single long column of pages, which
 * is a scroll through a document to look at one page of it - and the rail is the
 * answer to that: every page at a glance, its own scroll, and the page a reader
 * picked filling the space next to it. The rail scrolls on its own so that a
 * fifty-page deck does not push the stage down with it.
 *
 * The zoom control is at the bottom right, where a page's own scale belongs, and
 * starts fitted: a 960-pixel slide in a 700-pixel dialog is a preview of its corner.
 * The percentage is a button because two numbers people want are "the whole page"
 * and "actual size", and a fitted page is neither until the window changes.
 */
export function PagedDocument({
  pages,
  width,
  height,
  initialPage = 0,
  fitMode = "page",
}: PagedDocumentProps) {
  const t = useTranslations("files.office");
  // Held as asked for and clamped on read: a rewritten deck that has fewer pages
  // must not leave the stage on a page number nothing draws, and clamping in an
  // effect would render the missing page once before correcting itself.
  const [asked, setAsked] = useState(initialPage);
  const index = clamp(asked, pages.length);
  const [zoom, setZoom] = useState<number | "fit">("fit");
  const stage = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });

  // Fitted means fitted to the box it is in, which is a box that changes with the
  // window - so the box is measured rather than assumed.
  useEffect(() => {
    const element = stage.current;
    if (element === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry !== undefined)
        setBox({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const fit =
    box.width === 0 || box.height === 0
      ? 1
      : Math.min(
          Math.max(box.width - STAGE_GUTTER, 80) / width,
          fitMode === "page" ? Math.max(box.height - STAGE_GUTTER, 80) / height : 2,
          2,
        );
  const scale = zoom === "fit" ? fit : zoom;
  const page = pages[index] ?? "";
  const single = pages.length <= 1;

  function step(direction: 1 | -1): void {
    setZoom(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, round(scale + direction * ZOOM_STEP))));
  }

  return (
    <div className="border-border flex h-full min-h-0 overflow-hidden rounded-lg border">
      {/* A rail for one page is a column of width with nothing in it. */}
      {!single && (
        <aside
          aria-label={t("pagesAria")}
          style={{ width: RAIL_WIDTH }}
          className="bg-muted/30 border-border flex shrink-0 flex-col border-r"
        >
          <div className="min-h-0 flex-1 scrollbar-thin space-y-2 overflow-y-auto p-2">
            {pages.map((markup, page_number) => (
              <button
                key={page_number}
                type="button"
                onClick={() => setAsked(page_number)}
                aria-current={page_number === index ? "page" : undefined}
                aria-label={t("page", { number: page_number + 1 })}
                className={cn(
                  "block w-full rounded-md p-1 text-center transition-colors",
                  page_number === index
                    ? "bg-foreground/[0.08] ring-foreground/30 ring-1"
                    : "hover:bg-foreground/[0.04]",
                )}
              >
                <span
                  className="relative block overflow-hidden rounded-sm border border-black/10 bg-white shadow-sm"
                  style={{ width: THUMB_WIDTH, height: (THUMB_WIDTH * height) / width }}
                >
                  <span
                    className="absolute top-0 left-0 block origin-top-left"
                    style={{
                      width,
                      height,
                      transform: `scale(${THUMB_WIDTH / width})`,
                    }}
                    // Already sanitised by the reader: handlers, framing elements and
                    // script URLs are gone before this string exists.
                    dangerouslySetInnerHTML={{ __html: markup }}
                  />
                </span>
                <span
                  className={cn(
                    "mt-1 block text-[11px] tabular-nums",
                    page_number === index ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {page_number + 1}
                </span>
              </button>
            ))}
          </div>
        </aside>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div ref={stage} className="bg-muted/20 relative min-h-0 flex-1 overflow-auto p-4">
          <div
            className="mx-auto overflow-hidden bg-white shadow-md ring-1 ring-black/10"
            style={{ width: width * scale, height: height * scale }}
          >
            <div
              data-testid="page-stage"
              className="origin-top-left"
              style={{ width, height, transform: `scale(${scale})` }}
              dangerouslySetInnerHTML={{ __html: page }}
            />
          </div>
        </div>

        <div className="border-border flex shrink-0 items-center justify-between gap-2 border-t px-2 py-1.5">
          <div className="flex items-center gap-1">
            {!single && (
              <>
                <RailButton
                  label={t("previousPage")}
                  disabled={index === 0}
                  onClick={() => setAsked(index - 1)}
                >
                  <ChevronLeft className="h-4 w-4" />
                </RailButton>
                <span className="text-muted-foreground min-w-14 text-center text-xs tabular-nums">
                  {t("pageOfTotal", { page: index + 1, total: pages.length })}
                </span>
                <RailButton
                  label={t("nextPage")}
                  disabled={index === pages.length - 1}
                  onClick={() => setAsked(index + 1)}
                >
                  <ChevronRight className="h-4 w-4" />
                </RailButton>
              </>
            )}
          </div>

          {/* The zoom pill, at the edge a page's scale belongs to. */}
          <div className="border-border flex items-center gap-0.5 rounded-full border px-1 py-0.5">
            <RailButton label={t("zoomOut")} onClick={() => step(-1)} disabled={scale <= ZOOM_MIN}>
              <ZoomOut className="h-3.5 w-3.5" />
            </RailButton>
            <button
              type="button"
              onClick={() => setZoom(zoom === "fit" ? 1 : "fit")}
              title={zoom === "fit" ? t("actualSize") : t("fitToWindow")}
              className="text-muted-foreground hover:text-foreground w-12 rounded-full text-center text-xs tabular-nums transition-colors"
            >
              {Math.round(scale * 100)}%
            </button>
            <RailButton label={t("zoomIn")} onClick={() => step(1)} disabled={scale >= ZOOM_MAX}>
              <ZoomIn className="h-3.5 w-3.5" />
            </RailButton>
            <RailButton
              label={t("fitToWindow")}
              onClick={() => setZoom("fit")}
              active={zoom === "fit"}
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </RailButton>
          </div>
        </div>
      </div>
    </div>
  );
}

/** One of the viewer's quiet buttons: an arrow, a magnifier, a frame. */
function RailButton({
  label,
  onClick,
  disabled = false,
  active = false,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "text-muted-foreground hover:text-foreground hover:bg-accent/60 rounded-full p-1 transition-colors disabled:pointer-events-none disabled:opacity-30",
        active && "text-foreground bg-accent/60",
      )}
    >
      {children}
    </button>
  );
}

/** A page number the document still has. */
function clamp(page: number, count: number): number {
  return Math.min(Math.max(page, 0), Math.max(count - 1, 0));
}

/** Two decimals, so a hundred clicks do not become 1.3000000000000003. */
function round(value: number): number {
  return Math.round(value * 100) / 100;
}
