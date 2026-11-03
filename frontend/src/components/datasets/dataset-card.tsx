"use client";

import Link from "next/link";
import { ArrowUpRight, Database, Table2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge, Card, CardContent } from "@/components/ui";
import { ROUTES } from "@/lib/constants";
import type { Dataset } from "@/types/datasets";

interface DatasetCardProps {
  dataset: Dataset;
  kbId: string;
}

/** One dataset in a base: what it holds, how many versions, when it last moved. */
export function DatasetCard({ dataset, kbId }: DatasetCardProps) {
  const t = useTranslations("datasets");
  return (
    <Link
      href={`${ROUTES.DATASET_BASE(kbId)}?dataset=${dataset.id}`}
      className="group rounded-card focus-visible:ring-ring block focus-visible:ring-2 focus-visible:outline-none"
    >
      <Card className="hover:bg-accent/40 h-full transition-colors">
        <CardContent className="flex h-full flex-col gap-3 p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <Table2 className="text-muted-foreground size-4 shrink-0" aria-hidden />
              <span className="truncate font-medium">{dataset.name}</span>
            </div>
            <ArrowUpRight
              className="text-muted-foreground group-hover:text-foreground size-4 shrink-0 transition-colors"
              aria-hidden
            />
          </div>

          <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
            <Badge variant="secondary">{t("rowsCount", { count: dataset.row_count })}</Badge>
            <Badge variant="outline">
              {t("versionBadge", { version: dataset.current_version_no })}
            </Badge>
            <span className="truncate">{dataset.sheet_name ?? t("noSheet")}</span>
          </div>

          <p className="text-muted-foreground mt-auto line-clamp-2 text-sm">
            {dataset.fields
              .slice(0, 6)
              .map((field) => field.display_name)
              .join(" · ")}
            {dataset.fields.length > 6 ? " …" : ""}
          </p>
        </CardContent>
      </Card>
    </Link>
  );
}

export function DatasetBaseIcon() {
  return <Database className="size-4" aria-hidden />;
}
