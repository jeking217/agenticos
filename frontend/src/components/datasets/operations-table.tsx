"use client";

import { GitCompareArrows, RotateCcw } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge, Button, DataTable, type Column } from "@/components/ui";
import { useOperations, useRowMutations } from "@/hooks/use-datasets";
import type { Dataset, DatasetOperation } from "@/types/datasets";

const VARIANT = {
  insert: "secondary",
  update: "default",
  delete: "destructive",
  restore: "secondary",
  import: "outline",
  model: "outline",
} as const;

interface OperationsTableProps {
  dataset: Dataset;
  /** One record's history, when the drawer was opened from a row. */
  recordKey?: string;
  /** Jumps to the version list with this version on one side of a comparison. */
  onCompare?: (version: number) => void;
}

/**
 * What changed, when, by whom, and why.
 *
 * The reason is the column that matters: it is what a person reads months later
 * when a number they quoted has moved, and it is why the maintenance dialogs
 * refuse an empty one. A deleted row can be restored from here, which is the way
 * back for a deletion made by mistake.
 *
 * Shared by the tab and the drawer (#T45): the same log, asked for one record or
 * for the whole dataset, so the two entries cannot drift apart.
 */
export function OperationsTable({ dataset, recordKey, onCompare }: OperationsTableProps) {
  const t = useTranslations("datasets");
  const { operations, isLoading } = useOperations(dataset.id);
  const { restore } = useRowMutations(dataset.id);
  const rows = recordKey ? operations.filter((op) => op.record_key === recordKey) : operations;

  const columns: Column<DatasetOperation>[] = [
    {
      key: "version",
      header: t("version"),
      cell: (operation) => (
        <span className="flex items-center gap-1.5">
          <span className="tabular-nums">v{operation.version_no}</span>
          {onCompare && (
            <button
              type="button"
              onClick={() => onCompare(operation.version_no)}
              title={t("compareThisVersion", { version: operation.version_no })}
              aria-label={t("compareThisVersion", { version: operation.version_no })}
              className="text-muted-foreground hover:text-foreground"
            >
              <GitCompareArrows className="size-3.5" aria-hidden />
            </button>
          )}
        </span>
      ),
      className: "w-24",
    },
    {
      key: "operation",
      header: t("operation"),
      cell: (operation) => (
        <Badge variant={VARIANT[operation.operation]}>
          {t(`operationKind.${operation.operation}`)}
        </Badge>
      ),
      className: "w-28",
    },
    {
      key: "record",
      header: t("recordKey"),
      cell: (operation) => (
        <span className="text-muted-foreground font-mono text-xs">
          {operation.record_key ?? "—"}
        </span>
      ),
    },
    {
      key: "reason",
      header: t("reason"),
      cell: (operation) => operation.reason ?? "—",
      hideBelow: "md",
    },
    {
      key: "at",
      header: t("when"),
      cell: (operation) => (
        <span className="text-muted-foreground text-sm">
          {new Date(operation.created_at).toLocaleString()}
        </span>
      ),
      hideBelow: "lg",
    },
    {
      key: "actions",
      header: "",
      className: "w-24",
      cell: (operation) =>
        operation.operation === "delete" && operation.record_key ? (
          <Button
            size="sm"
            variant="outline"
            loading={restore.isPending}
            onClick={() =>
              restore.mutate({
                recordKey: operation.record_key as string,
                reason: t("restoreReason"),
              })
            }
          >
            <RotateCcw className="size-3.5" aria-hidden />
            {t("restore")}
          </Button>
        ) : null,
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={rows}
      loading={isLoading}
      getRowKey={(operation) => operation.id}
      empty={<p className="text-muted-foreground py-8 text-center text-sm">{t("noOperations")}</p>}
    />
  );
}
