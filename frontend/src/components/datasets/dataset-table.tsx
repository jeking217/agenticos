"use client";

import { useMemo } from "react";
import { History, MoreHorizontal, PencilLine, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  Badge,
  Button,
  DataTable,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  type Column,
} from "@/components/ui";
import type { Dataset, DatasetRow } from "@/types/datasets";

interface DatasetTableProps {
  dataset: Dataset;
  rows: DatasetRow[];
  loading: boolean;
  /** Shown beside a row read from an older version than the current one. */
  asOf: number | null;
  onEdit: (row: DatasetRow) => void;
  onDelete: (row: DatasetRow) => void;
  /** The row's history, opened over the table rather than in a tab (#T45). */
  onOperations?: (row: DatasetRow) => void;
  error?: unknown;
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "✓" : "✗";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

/**
 * The rows of one version, with the row's own version beside them.
 *
 * `record_version` is on every row because it answers a question the dataset
 * version cannot: this record was last changed at v4 and has been carried
 * forward unchanged since. Seeing it is what stops "the file did not mention it"
 * from reading as "it was deleted".
 */
export function DatasetTable({
  dataset,
  rows,
  loading,
  asOf,
  onEdit,
  onDelete,
  onOperations,
  error,
}: DatasetTableProps) {
  const t = useTranslations("datasets");

  const columns = useMemo<Column<DatasetRow>[]>(() => {
    const valueColumns: Column<DatasetRow>[] = dataset.fields.map((field) => ({
      key: field.name,
      header: (
        <span className="flex items-center gap-1.5">
          {field.display_name}
          {field.is_primary_key && (
            <Badge variant="outline" className="px-1 py-0 text-[10px]">
              {t("key")}
            </Badge>
          )}
        </span>
      ),
      cell: (row) => (
        <span className={field.data_type === "numeric" ? "tabular-nums" : undefined}>
          {renderValue(row.values[field.name])}
        </span>
      ),
      align: field.data_type === "numeric" ? "right" : "left",
      sortValue: (row) => {
        const value = row.values[field.name];
        return value === null || value === undefined ? null : (value as string | number);
      },
    }));

    return [
      ...valueColumns,
      {
        key: "__record_version",
        header: t("recordVersion"),
        cell: (row) => (
          <Badge
            variant={
              row.record_version < (asOf ?? dataset.current_version_no) ? "outline" : "secondary"
            }
          >
            v{row.record_version}
          </Badge>
        ),
        className: "w-24",
        hideBelow: "md",
      },
      {
        key: "__actions",
        header: "",
        className: "w-12",
        cell: (row) => (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={t("rowActions")}>
                <MoreHorizontal className="size-4" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => onEdit(row)}>
                <PencilLine className="size-4" aria-hidden />
                {t("editRow")}
              </DropdownMenuItem>
              <DropdownMenuItem
                className="text-destructive focus:text-destructive"
                onSelect={() => onDelete(row)}
              >
                <Trash2 className="size-4" aria-hidden />
                {t("deleteRow")}
              </DropdownMenuItem>
              {onOperations && (
                <DropdownMenuItem onSelect={() => onOperations(row)}>
                  <History className="size-4" aria-hidden />
                  {t("rowOperations")}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        ),
      },
    ];
  }, [dataset, asOf, onEdit, onDelete, onOperations, t]);

  return (
    <DataTable
      columns={columns}
      rows={rows}
      loading={loading}
      getRowKey={(row) => row.record_key}
      empty={<p className="text-muted-foreground py-10 text-center text-sm">{t("noRows")}</p>}
      error={
        error ? (
          <p className="text-destructive py-10 text-center text-sm">{t("rowsFailed")}</p>
        ) : null
      }
    />
  );
}
