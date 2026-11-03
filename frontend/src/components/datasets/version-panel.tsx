"use client";

import { useState } from "react";
import { GitCompareArrows, Pin } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  Badge,
  Button,
  DataTable,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  type Column,
} from "@/components/ui";
import { useDatasetVersions, useVersionCompare } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";
import type { Dataset, DatasetVersion, RowChange } from "@/types/datasets";

import { describeChange, formatCell } from "./version-diff";

const CHANGE_VARIANT = {
  added: "secondary",
  changed: "default",
  removed: "destructive",
  missing: "outline",
} as const;

interface VersionPanelProps {
  dataset: Dataset;
  /** A version the operation log asked to compare; it opens on that one. */
  fromVersion?: number | null;
}

/**
 * The version list, and a comparison on demand.
 *
 * Versions are listed newest first and never edited, so the only actions are
 * reading one and comparing two. "Pinned" means an answer was computed from it
 * and retention will keep it; it is not an action here.
 *
 * A comparison says which rows moved; selecting one says which *cells* did, in
 * two columns of before and after. `changed_fields` alone is a list of names -
 * "金额 changed" is the question, and "100 → 150" is the answer.
 */
export function VersionPanel({ dataset, fromVersion }: VersionPanelProps) {
  const t = useTranslations("datasets");
  const { versions, isLoading } = useDatasetVersions(dataset.id);
  const [from, setFrom] = useState<number | null>(fromVersion ?? null);
  // Against what the data says now, unless that is the version asked about.
  const [to, setTo] = useState<number | null>(
    fromVersion !== undefined && fromVersion !== null && fromVersion !== dataset.current_version_no
      ? dataset.current_version_no
      : null,
  );
  const { compare, isFetching } = useVersionCompare(dataset.id, from, to);
  const [open, setOpen] = useState<string | null>(null);

  const columns: Column<DatasetVersion>[] = [
    {
      key: "version",
      header: t("version"),
      cell: (version) => (
        <span className="flex items-center gap-2">
          <span className="font-medium tabular-nums">v{version.version_no}</span>
          {version.current && <Badge variant="secondary">{t("current")}</Badge>}
          {version.pinned && (
            <Pin className="text-muted-foreground size-3" aria-label={t("pinned")} />
          )}
        </span>
      ),
      className: "w-40",
    },
    {
      key: "kind",
      header: t("kind"),
      cell: (version) => <Badge variant="outline">{t(`versionKind.${version.kind}`)}</Badge>,
      className: "w-28",
    },
    {
      key: "counts",
      header: t("changes"),
      cell: (version) => (
        <span className="text-muted-foreground text-sm">
          +{version.rows_added} ~{version.rows_changed} −{version.rows_removed}
        </span>
      ),
    },
    {
      key: "source",
      header: t("source"),
      cell: (version) => (
        <span className="text-muted-foreground truncate text-sm">
          {version.source_filename ?? t(`versionKind.${version.kind}`)}
        </span>
      ),
      hideBelow: "md",
    },
    {
      key: "published",
      header: t("publishedAt"),
      cell: (version) => (
        <span className="text-muted-foreground text-sm">
          {new Date(version.published_at).toLocaleString()}
        </span>
      ),
      hideBelow: "lg",
    },
  ];

  const picked = compare?.rows.find((row) => row.record_key === open) ?? null;
  const cells = picked ? describeChange(picked) : [];

  const changed: Column<RowChange>[] = [
    {
      key: "record",
      header: t("recordKey"),
      cell: (row) => <span className="font-mono text-xs">{row.record_key}</span>,
    },
    {
      key: "change",
      header: t("changeKind"),
      cell: (row) => (
        <Badge variant={CHANGE_VARIANT[row.change]}>{t(`change.${row.change}`)}</Badge>
      ),
      className: "w-28",
    },
    {
      key: "fields",
      header: t("changedFields"),
      cell: (row) => (
        <span className="text-muted-foreground text-sm">
          {row.changed_fields.length > 0 ? row.changed_fields.join(", ") : "—"}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <span className="text-muted-foreground text-xs">{t("compareFrom")}</span>
          <Select
            value={from ? String(from) : ""}
            onValueChange={(value) => setFrom(Number(value))}
          >
            <SelectTrigger className="w-32" aria-label={t("compareFrom")}>
              <SelectValue placeholder="v—" />
            </SelectTrigger>
            <SelectContent>
              {versions.map((version) => (
                <SelectItem key={version.id} value={String(version.version_no)}>
                  v{version.version_no}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <span className="text-muted-foreground text-xs">{t("compareTo")}</span>
          <Select value={to ? String(to) : ""} onValueChange={(value) => setTo(Number(value))}>
            <SelectTrigger className="w-32" aria-label={t("compareTo")}>
              <SelectValue placeholder="v—" />
            </SelectTrigger>
            <SelectContent>
              {versions.map((version) => (
                <SelectItem key={version.id} value={String(version.version_no)}>
                  v{version.version_no}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="outline"
          onClick={() => {
            const latest = versions[0];
            const previous = versions[1];
            if (latest && previous) {
              setFrom(previous.version_no);
              setTo(latest.version_no);
            }
          }}
          disabled={versions.length < 2}
        >
          <GitCompareArrows className="size-4" aria-hidden />
          {t("compareLatest")}
        </Button>
      </div>

      {from !== null && to !== null && from !== to && (
        <div className="rounded-card border-border space-y-3 border p-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            {compare ? (
              <>
                <Badge variant="secondary">
                  {t("change.added")} {compare.added}
                </Badge>
                <Badge>
                  {t("change.changed")} {compare.changed}
                </Badge>
                <Badge variant="destructive">
                  {t("change.removed")} {compare.removed}
                </Badge>
                <Badge variant="outline">
                  {t("change.missing")} {compare.missing}
                </Badge>
                <span className="text-muted-foreground">
                  {t("unchangedCount", { count: compare.unchanged })}
                </span>
                {compare.truncated && (
                  <span className="text-muted-foreground">{t("compareTruncated")}</span>
                )}
              </>
            ) : (
              <span className="text-muted-foreground">{t("comparing")}</span>
            )}
          </div>
          {compare && compare.rows.length > 0 && (
            <DataTable
              columns={changed}
              rows={compare.rows}
              getRowKey={(row, index) => `${row.record_key}-${index}`}
              loading={isFetching}
              onRowClick={(row) => setOpen(open === row.record_key ? null : row.record_key)}
            />
          )}

          {picked && (
            <div
              className="rounded-card border-border space-y-2 border p-3"
              data-testid="version-cell-diff"
            >
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium">
                  {t("diffTitle", { record: picked.record_key })}
                </p>
                <Badge variant={CHANGE_VARIANT[picked.change]}>
                  {t(`change.${picked.change}`)}
                </Badge>
              </div>
              {cells.length > 0 ? (
                <table className="w-full text-left text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="w-40 pr-3 font-medium">{t("diffField")}</th>
                      <th className="pr-3 font-medium">{t("diffBefore")}</th>
                      <th className="font-medium">{t("diffAfter")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cells.map((cell) => (
                      <tr key={cell.field}>
                        <td className="text-muted-foreground pr-3 font-medium">{cell.field}</td>
                        <td
                          className={cn(
                            "pr-3",
                            cell.changed && "text-destructive line-through decoration-1",
                          )}
                        >
                          {formatCell(cell.before)}
                        </td>
                        <td
                          className={cn(cell.changed && "text-emerald-600 dark:text-emerald-400")}
                        >
                          {cell.changed ? formatCell(cell.after) : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-muted-foreground text-xs">{t("diffEmpty")}</p>
              )}
            </div>
          )}
        </div>
      )}

      <DataTable
        columns={columns}
        rows={versions}
        loading={isLoading}
        getRowKey={(version) => version.id}
        empty={<p className="text-muted-foreground py-8 text-center text-sm">{t("noVersions")}</p>}
      />
    </div>
  );
}
