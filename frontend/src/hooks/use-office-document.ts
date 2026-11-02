"use client";

import { useEffect, useState } from "react";

import {
  readWorkbook,
  renderOfficePages,
  type OfficeFormat,
  type RenderedDocument,
  type SheetTable,
} from "@/lib/office-document";

/**
 * One document, being read.
 *
 * Rendering is asynchronous twice over - the reader is imported, then it parses an
 * archive - so what a viewer has while it waits is a state rather than a value. Both
 * hooks keep the same contract: `null` until something is known, then either the
 * rendering or the reason there is none.
 *
 * **State is stamped with the file it belongs to** rather than cleared when the file
 * changes. Clearing is a `setState` in the effect body, which renders once to show
 * nothing and once to show the new document; holding the stamp means the old
 * rendering is simply not the answer to this file, and a viewer that flickers through
 * a blank frame every time an agent rewrites the deck does not have to.
 */

interface UseRenderedDocumentResult {
  document: RenderedDocument | null;
  isLoading: boolean;
  /** Whether the file could not be read at all, said as a sentence by the caller. */
  failed: boolean;
}

/** A deck or a document, as pages. */
export function useRenderedDocument(
  format: Exclude<OfficeFormat, "sheet"> | null,
  file: Blob | null,
): UseRenderedDocumentResult {
  const [rendered, setRendered] = useState<{ file: Blob; document: RenderedDocument } | null>(null);
  const [unreadable, setUnreadable] = useState<Blob | null>(null);

  useEffect(() => {
    if (format === null || file === null) return;
    // A renderer that finishes after the reader moved on must not hand its pages to
    // the next document, and must release what it attached where it can.
    let current = true;
    let drawn: RenderedDocument | null = null;

    renderOfficePages(format, file)
      .then((document) => {
        drawn = document;
        if (!current) {
          document.dispose();
          return;
        }
        setRendered({ file, document });
      })
      .catch(() => {
        if (current) setUnreadable(file);
      });

    return () => {
      current = false;
      drawn?.dispose();
    };
  }, [format, file]);

  const document = file !== null && rendered !== null && rendered.file === file ? rendered : null;
  const failed = file !== null && unreadable === file;

  return {
    document: document?.document ?? null,
    isLoading: file !== null && format !== null && document === null && !failed,
    failed,
  };
}

interface UseWorkbookResult {
  sheets: SheetTable[] | null;
  isLoading: boolean;
  failed: boolean;
}

/** A workbook, as the sheet grids it holds. */
export function useWorkbook(file: Blob | null): UseWorkbookResult {
  const [read, setRead] = useState<{ file: Blob; sheets: SheetTable[] } | null>(null);
  const [unreadable, setUnreadable] = useState<Blob | null>(null);

  useEffect(() => {
    if (file === null) return;
    let current = true;
    readWorkbook(file)
      .then((sheets) => {
        if (current) setRead({ file, sheets });
      })
      .catch(() => {
        if (current) setUnreadable(file);
      });
    return () => {
      current = false;
    };
  }, [file]);

  const sheets = file !== null && read !== null && read.file === file ? read.sheets : null;

  return {
    sheets,
    isLoading: file !== null && sheets === null && unreadable !== file,
    failed: file !== null && unreadable === file,
  };
}
