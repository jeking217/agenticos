"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { Skeleton } from "@/components/ui";
import { useFileBlob } from "@/hooks";
import { useRenderedDocument, useWorkbook } from "@/hooks/use-office-document";
import type { FileAccess } from "@/lib/file-access";
import type { OfficeFormat } from "@/lib/office-document";
import { cn } from "@/lib/utils";

interface OfficePageGridProps {
  access: FileAccess;
  name: string;
  format: OfficeFormat;
  /** Opening the document, on the page that was picked. */
  onOpen: (page?: number) => void;
  /** Pages drawn before the rest are behind one tile. */
  limit?: number;
}

/** The gap between tiles, in pixels. Mirrors `gap-2` below. */
const GAP = 8;

/**
 * A document made during a turn, as its pages, in the transcript.
 *
 * The step above says *Wrote the deck*; this is what it looks like, at a size
 * somebody can tell two slides apart at. Three to a row for a deck - the shape a
 * slide has - and two for a document, whose pages are taller than they are wide.
 *
 * It is here rather than only behind the Open button because a deck being built is
 * the thing being watched: a preview squeezed into a thumbnail column, or a page per
 * row at a sixth of the width, is a preview that answers "did it work" and not "is it
 * right".
 */
export function OfficePageGrid({ access, name, format, onOpen, limit = 6 }: OfficePageGridProps) {
  const t = useTranslations("files.office");
  const tFiles = useTranslations("files");
  const { blob, isLoading: fetching, error: readError } = useFileBlob(access);
  const rendered = useRenderedDocument(format === "sheet" ? null : format, blob);
  const workbook = useWorkbook(format === "sheet" ? blob : null);
  // A callback ref rather than a ref object: the row is drawn twice - once as a
  // skeleton, once as tiles - and an effect keyed on nothing would keep measuring
  // the element that was replaced.
  const [grid, setGrid] = useState<HTMLUListElement | null>(null);
  const [gridWidth, setGridWidth] = useState(0);

  // A tile's width is a third of the row it is in, and the row is whatever the
  // transcript is wide - so it is measured rather than pinned.
  useEffect(() => {
    if (grid === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry !== undefined) setGridWidth(entry.contentRect.width);
    });
    observer.observe(grid);
    return () => observer.disconnect();
  }, [grid]);

  // A preview that cannot be read draws nothing rather than an error: the step above
  // it already names the file, and the Open button beside it is where a refusal goes.
  if (readError !== null) return null;

  if (format === "sheet") {
    if (workbook.failed) return null;
    if (workbook.sheets === null) return <Skeleton className="h-24 w-full" />;
    const sheet = workbook.sheets[0];
    if (sheet === undefined) return null;
    return (
      <button
        type="button"
        onClick={() => onOpen()}
        aria-label={tFiles("openFile", { name })}
        className="hover:border-foreground/30 block w-full overflow-hidden rounded-lg border text-left transition-colors"
      >
        <SheetPeek rows={sheet.rows} name={sheet.name || name} />
      </button>
    );
  }

  if (rendered.failed) return null;
  if (rendered.document === null) {
    // Nothing fetching and nothing read: the file is gone, and a skeleton would say
    // it is on its way.
    if (!fetching && blob === null) return null;
    // The frame keeps its height while the pages are read, so a transcript does not
    // jump when they arrive.
    return (
      <ul
        ref={setGrid}
        className={cn("grid", columnsFor(format) === 3 ? "grid-cols-3" : "grid-cols-2")}
      >
        <li>
          <Skeleton className="aspect-video w-full" />
        </li>
      </ul>
    );
  }

  const { pages, width, height } = rendered.document;
  const columns = columnsFor(format);
  const tileWidth = Math.max((gridWidth - GAP * (columns - 1)) / columns, 0);
  // A document's page is taller than it is wide and its content often stops a third
  // of the way down, so the tile shows the top of the page rather than half a metre
  // of paper. A slide is drawn whole: it is one picture.
  const fullHeight = tileWidth * (height / width);
  const tileHeight =
    format === "doc" ? Math.min(fullHeight, Math.round(tileWidth * 0.6)) : fullHeight;
  const cropped = tileHeight < fullHeight - 1;
  const shown = pages.slice(0, limit);
  const rest = pages.length - shown.length;

  return (
    <ul
      ref={setGrid}
      className={cn("grid gap-2", columns === 3 ? "grid-cols-3" : "grid-cols-2")}
      aria-label={t("pagesAria")}
    >
      {shown.map((markup, index) => (
        <li key={index}>
          <button
            type="button"
            onClick={() => onOpen(index)}
            aria-label={t("openPage", { number: index + 1 })}
            className={cn(
              "group hover:border-foreground/30 relative block w-full overflow-hidden rounded-lg border bg-white transition-colors",
              // The rest of the page is not shown, and a hard cut through a line of
              // text reads as a broken render - so it fades.
              cropped &&
                "[mask-image:linear-gradient(to_bottom,black_72%,transparent)] [-webkit-mask-image:linear-gradient(to_bottom,black_72%,transparent)]",
            )}
            style={{ height: tileHeight }}
          >
            <span
              className="absolute top-0 left-0 block origin-top-left"
              style={{ width, height, transform: `scale(${tileWidth / width})` }}
              // Sanitised by the reader before it became a string.
              dangerouslySetInnerHTML={{ __html: markup }}
            />
            <span className="bg-foreground/70 absolute right-1 bottom-1 rounded px-1 text-[10px] text-white tabular-nums opacity-0 transition-opacity group-hover:opacity-100">
              {index + 1}
            </span>
          </button>
        </li>
      ))}
      {rest > 0 && (
        <li>
          <button
            type="button"
            onClick={() => onOpen(shown.length)}
            className="border-border text-muted-foreground hover:text-foreground hover:border-foreground/30 flex w-full items-center justify-center rounded-lg border border-dashed text-sm transition-colors"
            style={{ height: tileHeight }}
          >
            {t("morePages", { count: rest })}
          </button>
        </li>
      )}
    </ul>
  );
}

/** A slide is wide, a page is tall: the row is cut to the shape of what it holds. */
function columnsFor(format: OfficeFormat): 2 | 3 {
  return format === "deck" ? 3 : 2;
}

/** The top of a workbook, as a card: the grid itself is in the viewer. */
function SheetPeek({ rows, name }: { rows: string[][]; name: string }) {
  const preview = rows.slice(0, 4);
  return (
    <div className="bg-background">
      <table aria-hidden className="w-full table-fixed text-[11px]">
        <tbody>
          {preview.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-border/60 border-b last:border-0">
              {row.slice(0, 4).map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className={cn(
                    "truncate px-2 py-1",
                    rowIndex === 0 && cellIndex === 0
                      ? "text-foreground font-medium"
                      : "text-foreground/80",
                  )}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-muted-foreground truncate px-2 pb-1.5 text-[11px]">{name}</p>
    </div>
  );
}
