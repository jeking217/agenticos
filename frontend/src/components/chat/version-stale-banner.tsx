"use client";

import { History, TriangleAlert } from "lucide-react";
import { useTranslations } from "next-intl";

import { useDatasetVersionStatus } from "@/hooks/use-dataset-version-status";
import { datasetPath, type SourceDocument } from "@/lib/chat-sources";
import { useRouter } from "@/lib/locale-navigation";
import { cn } from "@/lib/utils";

/** The same page, on another tab: the versions it kept, or what was done to it. */
function tabPath(
  doc: SourceDocument,
  tab: "versions" | "operations",
  version?: number,
): string | null {
  const path = datasetPath({ ...doc, version });
  return path === null ? null : `${path}&tab=${tab}`;
}

const STRIP =
  "border-foreground/10 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2 text-xs";
const ACTION =
  "font-medium text-foreground/80 hover:text-foreground underline-offset-2 hover:underline";

/**
 * What a historical message says about the data it was answered from (#T23).
 *
 * The answer itself is never rewritten: the tool result was frozen when it ran,
 * and replaying the conversation replays those numbers. What this adds is the
 * one thing a reader cannot see for themselves - that the data has moved on,
 * that the version is no longer queryable, or that the dataset is gone - and the
 * way back to the data as it stands.
 *
 * "View the latest" is a view state, not a new answer: it points the citation at
 * the current version, and the button becomes the way back to what was read.
 */
export function DatasetNotice({
  doc,
  viewing,
  onView,
  onDismiss,
}: {
  doc: SourceDocument;
  /** Whether the citation is pointed at the latest version instead of its own. */
  viewing: boolean;
  onView: (latest: boolean) => void;
  onDismiss: () => void;
}) {
  const t = useTranslations("chat");
  const router = useRouter();
  const status = useDatasetVersionStatus(doc, { withOperations: true });

  if (status.state === "unknown" || status.state === "pending" || status.state === "current") {
    return null;
  }

  if (status.state === "gone") {
    return (
      <p
        data-testid="dataset-notice-gone"
        className={cn(STRIP, "border-foreground/12 bg-muted/40 text-muted-foreground")}
      >
        {t("versionGone")}
      </p>
    );
  }

  const frozen = datasetPath({ ...doc, version: status.version });
  const compare = tabPath(doc, "versions");
  const log = tabPath(doc, "operations");

  if (status.state === "archived") {
    return (
      <div
        data-testid="dataset-notice-archived"
        className={cn(STRIP, "border-foreground/12 bg-muted/40 text-muted-foreground")}
      >
        <History className="size-3.5 shrink-0" aria-hidden />
        <span className="min-w-0 flex-1">
          {t("versionArchived", { version: status.version ?? 0 })}
        </span>
        {frozen && (
          <button type="button" className={ACTION} onClick={() => router.push(frozen)}>
            {t("readOnly")}
          </button>
        )}
      </div>
    );
  }

  // N2, and N5 on top of it - a stale number usually raises "was this record
  // deleted?", which the operation log answers.
  const deleted = status.deletedSince ?? 0;

  return (
    <div
      data-testid={deleted > 0 ? "dataset-notice-deleted" : "dataset-notice-moved"}
      className={cn(
        STRIP,
        deleted > 0
          ? "border-destructive/30 bg-destructive/5 text-foreground"
          : "text-foreground border-amber-500/30 bg-amber-500/5",
      )}
    >
      {deleted > 0 && <TriangleAlert className="text-destructive size-3.5 shrink-0" aria-hidden />}
      <span className="min-w-0 flex-1">
        {deleted > 0
          ? t("versionDeletedRecords", { count: deleted, version: status.version ?? 0 })
          : status.publishedAt
            ? t("versionMovedOn", {
                latest: status.latest ?? 0,
                version: status.version ?? 0,
                date: new Date(status.publishedAt).toLocaleDateString(),
              })
            : t("versionMovedOnUndated", {
                latest: status.latest ?? 0,
                version: status.version ?? 0,
              })}
        {viewing && status.latest !== undefined && (
          <span className="text-muted-foreground">
            {" "}
            {t("viewingLatest", { latest: status.latest })}
          </span>
        )}
      </span>

      <button type="button" className={ACTION} onClick={() => onView(!viewing)}>
        {viewing ? t("viewFrozen", { version: status.version ?? 0 }) : t("viewLatest")}
      </button>
      {deleted > 0
        ? log && (
            <button type="button" className={ACTION} onClick={() => router.push(log)}>
              {t("viewOperations")}
            </button>
          )
        : compare && (
            <button type="button" className={ACTION} onClick={() => router.push(compare)}>
              {t("compareVersions")}
            </button>
          )}
      {deleted > 0 && frozen && (
        <button type="button" className={ACTION} onClick={() => router.push(frozen)}>
          {t("readOnly")}
        </button>
      )}
      {!viewing && (
        <button type="button" className={ACTION} onClick={onDismiss}>
          {t("keepFrozen", { version: status.version ?? 0 })}
        </button>
      )}
    </div>
  );
}
