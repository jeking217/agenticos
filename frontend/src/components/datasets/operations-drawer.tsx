"use client";

import { History } from "lucide-react";
import { useTranslations } from "next-intl";

import { Sheet, SheetClose, SheetContent, SheetHeader, SheetTitle } from "@/components/ui";
import type { Dataset } from "@/types/datasets";

import { OperationsTable } from "./operations-table";

interface OperationsDrawerProps {
  dataset: Dataset;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The record whose history was asked for, when a row's menu opened this. */
  recordKey?: string;
  /** Jumps to the version list with this version one side of a comparison. */
  onCompare?: (version: number) => void;
}

/**
 * The operation log over the page, without leaving it (#T45).
 *
 * The tab answers "what has been done to this dataset", but the question that
 * makes somebody look usually starts at one row - "who deleted this, and why" -
 * and a tab cannot be opened *on* a row without losing the table underneath.
 * The drawer stands at three doors: a row's menu, the dataset's own toolbar, and
 * a historical conversation that cited the data.
 */
export function OperationsDrawer({
  dataset,
  open,
  onOpenChange,
  recordKey,
  onCompare,
}: OperationsDrawerProps) {
  const t = useTranslations("datasets");

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:w-[36rem]">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <History className="text-muted-foreground size-4" aria-hidden />
            {t("operationsTitle")}
          </SheetTitle>
          <SheetClose onClick={() => onOpenChange(false)} />
        </SheetHeader>
        <div className="min-h-0 flex-1 space-y-3 p-4">
          <p className="text-muted-foreground text-sm">
            {recordKey
              ? t("operationsForRecord", { key: recordKey })
              : t("operationsForDataset", { name: dataset.name })}
          </p>
          <OperationsTable
            dataset={dataset}
            recordKey={recordKey}
            onCompare={
              onCompare
                ? (version) => {
                    onOpenChange(false);
                    onCompare(version);
                  }
                : undefined
            }
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}
