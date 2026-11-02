"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { FileUnavailable } from "./file-render";
import { PagedDocument } from "./paged-document";
import { Skeleton } from "@/components/ui";
import { useFileActions, useFileBlob } from "@/hooks";
import { useRenderedDocument, useWorkbook } from "@/hooks/use-office-document";
import type { FileAccess } from "@/lib/file-access";
import type { OfficeFormat, SheetTable } from "@/lib/office-document";
import { cn } from "@/lib/utils";

interface OfficePreviewProps {
  access: FileAccess;
  /** The name, for the download offer and nothing else. */
  name: string;
  format: OfficeFormat;
  /** The page to open on, where a reader arrived from a thumbnail of it. */
  initialPage?: number;
}

/**
 * A `.pptx`, `.docx` or `.xlsx`, read in the browser and shown as itself.
 *
 * These three used to end at the same sentence - "this one cannot be shown here, the
 * server serves it as a file" - which was true of the *server* and never of the
 * reader: a deck somebody's agent just made is the file they opened the viewer for.
 * The bytes are already fetched, so the reading happens here and the API's inline
 * allowlist stays exactly as it was.
 *
 * A deck and a document are pages and get the rail; a workbook is sheets and gets a
 * table. Pretending a spreadsheet has pages would be a rail of one.
 */
export function OfficePreview({ access, name, format, initialPage }: OfficePreviewProps) {
  const t = useTranslations("files.office");
  const { blob, isLoading: fetching, error: readError } = useFileBlob(access);
  const { download, error: actionError } = useFileActions(access);
  // Both hooks are called, and one of them is handed nothing: hooks do not branch,
  // and a sheet is not a deck that failed to render.
  const rendered = useRenderedDocument(format === "sheet" ? null : format, blob);
  const workbook = useWorkbook(format === "sheet" ? blob : null);

  if (readError !== null)
    return <FileUnavailable reason={readError} onDownload={download} error={actionError} />;

  if (format === "sheet") {
    if (workbook.failed) {
      return <FileUnavailable reason={t("failed")} onDownload={download} error={actionError} />;
    }
    if (workbook.isLoading || workbook.sheets === null)
      return <Skeleton className="h-full min-h-64 w-full" />;
    return <WorkbookPreview sheets={workbook.sheets} name={name} />;
  }

  if (rendered.failed)
    return <FileUnavailable reason={t("failed")} onDownload={download} error={actionError} />;
  if (rendered.isLoading || rendered.document === null || fetching)
    return <Skeleton className="h-full min-h-64 w-full" />;

  return (
    <PagedDocument
      pages={rendered.document.pages}
      width={rendered.document.width}
      height={rendered.document.height}
      initialPage={initialPage}
      // A document is read, so it fills the width; a deck is looked at, so the whole
      // slide is on screen.
      fitMode={format === "doc" ? "width" : "page"}
    />
  );
}

/** A workbook: its sheets down the side when it has more than one, a grid beside. */
function WorkbookPreview({ sheets, name }: { sheets: SheetTable[]; name: string }) {
  const t = useTranslations("files.office");
  const [active, setActive] = useState(0);
  const sheet = sheets[active] ?? sheets[0];

  if (sheet === undefined) return <p className="text-muted-foreground p-4 text-sm">{t("empty")}</p>;

  return (
    <div className="border-border flex h-full min-h-0 overflow-hidden rounded-lg border">
      {sheets.length > 1 && (
        <aside
          aria-label={t("sheetsAria")}
          className="bg-muted/30 border-border w-40 shrink-0 scrollbar-thin overflow-y-auto border-r p-1.5"
        >
          <ul className="space-y-0.5">
            {sheets.map((entry, index) => (
              <li key={entry.name}>
                <button
                  type="button"
                  onClick={() => setActive(index)}
                  aria-current={index === active ? "true" : undefined}
                  title={entry.name}
                  className={cn(
                    "w-full truncate rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                    index === active
                      ? "bg-foreground/[0.08] text-foreground font-medium"
                      : "text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground",
                  )}
                >
                  {entry.name}
                </button>
              </li>
            ))}
          </ul>
        </aside>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 scrollbar-thin overflow-auto">
          {/* No header row is assumed. A workbook's first row is a header in some
              sheets and a record in others, and a preview that guesses wrong reads
              as the file being wrong - so every row is a row, numbered down the
              side the way a spreadsheet numbers them. */}
          <table
            aria-label={t("sheetNamed", { name: sheet.name || name })}
            className="min-w-full border-separate border-spacing-0 text-xs"
          >
            <tbody>
              {sheet.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="hover:bg-accent/40">
                  <td
                    aria-hidden
                    className="bg-muted/70 text-muted-foreground border-border sticky left-0 border-r border-b px-1.5 py-1 text-right tabular-nums select-none"
                  >
                    {rowIndex + 1}
                  </td>
                  {row.map((cell, cellIndex) => (
                    <td
                      key={cellIndex}
                      className="border-border text-foreground/90 border-r border-b px-2.5 py-1 align-top break-words"
                      style={{ minWidth: "4rem", maxWidth: "24rem" }}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {sheet.rowsTotal > sheet.rows.length && (
          <p className="border-border text-muted-foreground shrink-0 border-t px-3 py-1.5 text-xs">
            {t("rowsShown", { shown: sheet.rows.length, total: sheet.rowsTotal })}
          </p>
        )}
      </div>
    </div>
  );
}
