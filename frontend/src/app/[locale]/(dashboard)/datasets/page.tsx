"use client";

import Link from "next/link";
import { Database, Table2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { CreateDatasetBaseDialog } from "@/components/datasets";
import { PageHeader } from "@/components/dashboard/page-header";
import { EmptyState, ErrorState } from "@/components/states";
import { Badge, Card, CardContent, ListCard, Skeleton } from "@/components/ui";
import { useDatasetBases } from "@/hooks/use-datasets";
import { ROUTES } from "@/lib/constants";

/**
 * The dataset bases: knowledge bases that hold rows rather than documents.
 *
 * They live on their own page rather than beside the document bases because
 * almost nothing they offer is the same - no upload of a PDF, no ingestion, no
 * search by meaning - and a shared list would be a list of two unrelated things
 * with one set of buttons that only fits half of them.
 */
export default function DatasetsPage() {
  const t = useTranslations("pages.datasets");
  const { bases, isLoading, error, refetch } = useDatasetBases();

  return (
    <div className="flex flex-1 flex-col space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<CreateDatasetBaseDialog />}
      />

      {error ? (
        <ErrorState
          title={t("loadFailed")}
          description={t("loadFailedHint")}
          cta={{ label: t("retry"), onClick: () => void refetch() }}
        />
      ) : isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <Skeleton key={index} className="rounded-card h-36" />
          ))}
        </div>
      ) : bases.length === 0 ? (
        <EmptyState icon={Database} title={t("emptyTitle")} description={t("emptyDescription")} />
      ) : (
        <ListCard fill title={t("bases")} counted={t("baseCount", { count: bases.length })}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {bases.map((base) => (
              <Link
                key={base.id}
                href={ROUTES.DATASET_BASE(base.id)}
                className="group rounded-card focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none"
              >
                <Card className="group-hover:bg-accent/40 h-full transition-colors">
                  <CardContent className="flex h-full flex-col gap-3 p-5">
                    <div className="flex items-center gap-2">
                      <Table2 className="text-muted-foreground size-4 shrink-0" aria-hidden />
                      <span className="truncate font-medium">{base.name}</span>
                    </div>
                    <p className="text-muted-foreground line-clamp-2 text-sm">
                      {base.description ?? t("noDescription")}
                    </p>
                    <div className="mt-auto flex items-center gap-2">
                      <Badge variant="outline">
                        {base.scope === "org" ? t("scopeOrg") : t("scopePersonal")}
                      </Badge>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        </ListCard>
      )}
    </div>
  );
}
