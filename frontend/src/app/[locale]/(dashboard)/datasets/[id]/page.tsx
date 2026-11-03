"use client";

import { use, useMemo, useState } from "react";
import {
  Archive,
  Database,
  History,
  Import,
  Pencil,
  Search,
  Table2,
  Waypoints,
} from "lucide-react";
import { useTranslations } from "next-intl";

import {
  DatasetTable,
  FieldEditorDialog,
  ImportWorkbookDialog,
  OperationsDrawer,
  OperationsPanel,
  RelationPanel,
  RowEditDialog,
  VersionPanel,
  type RowAction,
} from "@/components/datasets";
import { PageHeader } from "@/components/dashboard/page-header";
import { EmptyState, ErrorState } from "@/components/states";
import {
  Badge,
  Button,
  ConfirmDialog,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui";
import {
  useArchiveDataset,
  useDatasetBases,
  useDatasetRows,
  useDatasets,
  useDatasetVersions,
} from "@/hooks/use-datasets";
import { useUrlState } from "@/hooks";
import { cn } from "@/lib/utils";
import type { Dataset, DatasetRow } from "@/types/datasets";

type DatasetTab = "data" | "fields" | "versions" | "relations" | "operations";

const TABS: DatasetTab[] = ["data", "fields", "versions", "relations", "operations"];
const PAGE_SIZE = 50;

interface DatasetBasePageProps {
  params: Promise<{ id: string }>;
}

function parseTab(value: string | null): DatasetTab {
  return TABS.includes(value as DatasetTab) ? (value as DatasetTab) : "data";
}

/**
 * One dataset base: its datasets on the left, the chosen one on the right.
 *
 * Every view of the dataset is a version of it - the rows, the model, the
 * comparison, the operations - which is why the version selector lives in the
 * toolbar rather than in a tab: reading v3's rows and v3's changes is the same
 * question asked two ways.
 */
export default function DatasetBasePage({ params }: DatasetBasePageProps) {
  const t = useTranslations("pages.datasets");
  const { id } = use(params);

  const { bases } = useDatasetBases();
  const base = bases.find((entry) => entry.id === id);

  const { datasets, isLoading, error, refetch } = useDatasets(id);
  const [datasetParam, setDatasetParam] = useUrlState("dataset");
  const selected: Dataset | undefined =
    datasets.find((entry) => entry.id === datasetParam) ?? datasets[0];

  const [tabParam, setTabParam] = useUrlState("tab");
  const tab = parseTab(tabParam);

  const [importOpen, setImportOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [rowAction, setRowAction] = useState<{ row: DatasetRow; action: RowAction } | null>(null);
  // The operation log over the page (#T45), for the dataset or for one record.
  const [operations, setOperations] = useState<{ recordKey?: string } | null>(null);
  // A version the log or a notice asked to compare; the version tab opens on it.
  const [compareFrom, setCompareFrom] = useState<number | null>(null);

  return (
    <div className="flex flex-1 flex-col space-y-6">
      <PageHeader
        title={base?.name ?? t("title")}
        description={base?.description ?? t("description")}
        actions={
          <Button onClick={() => setImportOpen(true)} disabled={datasets.length === 0 && isLoading}>
            <Import className="size-4" aria-hidden />
            {t("importWorkbook")}
          </Button>
        }
        badges={<Badge variant="outline">{t("baseCount", { count: datasets.length })}</Badge>}
      />

      {error ? (
        <ErrorState
          title={t("loadFailed")}
          description={t("loadFailedHint")}
          cta={{ label: t("retry"), onClick: () => void refetch() }}
        />
      ) : isLoading ? (
        <Skeleton className="rounded-card h-64" />
      ) : datasets.length === 0 ? (
        <EmptyState
          icon={Database}
          title={t("noDatasetsTitle")}
          description={t("noDatasetsDescription")}
          cta={{ label: t("importWorkbook"), onClick: () => setImportOpen(true) }}
        />
      ) : (
        <div className="grid min-h-0 flex-1 gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
          <aside className="space-y-1">
            <p className="text-muted-foreground px-2 pb-1 text-xs font-medium tracking-wide uppercase">
              {t("datasets")}
            </p>
            {datasets.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => setDatasetParam(entry.id)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors",
                  entry.id === selected?.id
                    ? "bg-accent text-accent-foreground"
                    : "hover:bg-accent/50",
                )}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <Table2 className="text-muted-foreground size-4 shrink-0" aria-hidden />
                  <span className="truncate">{entry.name}</span>
                </span>
                <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                  v{entry.current_version_no}
                </span>
              </button>
            ))}
          </aside>

          <section className="min-w-0">
            {selected && (
              <DatasetWorkspace
                key={selected.id}
                dataset={selected}
                kbId={id}
                siblings={datasets.filter((entry) => entry.id !== selected.id)}
                tab={tab}
                onTabChange={(next) => setTabParam(next === "data" ? null : next)}
                onEditModel={() => setModelOpen(true)}
                onRowAction={(row, action) => setRowAction({ row, action })}
                onOperations={(rowKey) => setOperations({ recordKey: rowKey })}
                onOpenOperations={() => setOperations({})}
                compareFrom={compareFrom}
              />
            )}
          </section>
        </div>
      )}

      <ImportWorkbookDialog
        kbId={id}
        open={importOpen}
        onOpenChange={setImportOpen}
        datasets={datasets}
      />

      {selected && operations && (
        <OperationsDrawer
          dataset={selected}
          open
          recordKey={operations.recordKey}
          onOpenChange={(open) => {
            if (!open) setOperations(null);
          }}
          onCompare={(version) => {
            setCompareFrom(version);
            setTabParam("versions");
          }}
        />
      )}

      {selected && modelOpen && (
        <FieldEditorDialog
          key={selected.id}
          dataset={selected}
          open={modelOpen}
          onOpenChange={setModelOpen}
        />
      )}

      {selected && rowAction && (
        <RowEditDialog
          key={`${selected.id}:${rowAction.row.record_key}`}
          dataset={selected}
          row={rowAction.row}
          action={rowAction.action}
          onOpenChange={(open) => {
            if (!open) setRowAction(null);
          }}
        />
      )}
    </div>
  );
}

interface DatasetWorkspaceProps {
  dataset: Dataset;
  kbId: string;
  siblings: Dataset[];
  tab: DatasetTab;
  onTabChange: (tab: DatasetTab) => void;
  onEditModel: () => void;
  onRowAction: (row: DatasetRow, action: RowAction) => void;
  /** A row's history, as the row's own menu asks for it (#T45). */
  onOperations: (recordKey: string) => void;
  /** The whole dataset's history, from the toolbar beside its name. */
  onOpenOperations: () => void;
  /** The version the log asked to compare, which the version tab opens on. */
  compareFrom: number | null;
}

function DatasetWorkspace({
  dataset,
  kbId,
  siblings,
  tab,
  onTabChange,
  onEditModel,
  onRowAction,
  onOperations,
  onOpenOperations,
  compareFrom,
}: DatasetWorkspaceProps) {
  const t = useTranslations("pages.datasets");
  const tDatasets = useTranslations("datasets");
  const [versionParam, setVersionParam] = useUrlState("version");
  const [search, setSearch] = useState("");
  const [skip, setSkip] = useState(0);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const archive = useArchiveDataset(kbId);

  const { versions } = useDatasetVersions(dataset.id);
  const version = versionParam ? Number(versionParam) : null;
  const { rows, total, isLoading, error } = useDatasetRows(dataset.id, {
    version,
    search,
    skip,
    limit: PAGE_SIZE,
  });

  const historical = useMemo(
    () => (version !== null ? versions.find((entry) => entry.version_no === version) : undefined),
    [version, versions],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold">{dataset.name}</h2>
          <Badge variant="secondary">{t("rowsCount", { count: dataset.row_count })}</Badge>
          <Badge variant="outline">{t("modelVersion", { version: dataset.model_version })}</Badge>
          {dataset.last_import_at && (
            <span className="text-muted-foreground text-xs">
              {t("lastImport", { when: new Date(dataset.last_import_at).toLocaleDateString() })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onEditModel}>
            <Pencil className="size-3.5" aria-hidden />
            {t("editModel")}
          </Button>
          <Button variant="outline" size="sm" onClick={onOpenOperations}>
            <History className="size-3.5" aria-hidden />
            {tDatasets("operationsTitle")}
          </Button>
          <ConfirmDialog
            open={confirmArchive}
            onOpenChange={setConfirmArchive}
            title={tDatasets("archiveTitle")}
            description={tDatasets("archiveDescription")}
            confirmLabel={tDatasets("archiveAction")}
            destructive
            loading={archive.isPending}
            onConfirm={async () => {
              await archive.mutateAsync(dataset.id);
              setConfirmArchive(false);
            }}
          />
          <Button variant="ghost" size="sm" onClick={() => setConfirmArchive(true)}>
            <Archive className="size-3.5" aria-hidden />
            {tDatasets("archiveAction")}
          </Button>
        </div>
      </div>

      <Tabs value={tab} onValueChange={(next) => onTabChange(next as DatasetTab)}>
        <TabsList>
          <TabsTrigger value="data">
            <Table2 className="size-4 shrink-0" aria-hidden />
            {t("tabData")}
          </TabsTrigger>
          <TabsTrigger value="fields">
            <Database className="size-4 shrink-0" aria-hidden />
            {t("tabFields")}
          </TabsTrigger>
          <TabsTrigger value="versions">
            <History className="size-4 shrink-0" aria-hidden />
            {t("tabVersions")}
          </TabsTrigger>
          <TabsTrigger value="relations">
            <Waypoints className="size-4 shrink-0" aria-hidden />
            {t("tabRelations")}
          </TabsTrigger>
          <TabsTrigger value="operations">{t("tabOperations")}</TabsTrigger>
        </TabsList>

        <TabsContent
          value="data"
          className="space-y-3 data-[state=active]:flex data-[state=active]:flex-col"
        >
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative w-64">
              <Search
                className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2"
                aria-hidden
              />
              <Input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setSkip(0);
                }}
                placeholder={t("searchRows")}
                className="pl-8"
              />
            </div>

            <Select
              value={versionParam ?? "current"}
              onValueChange={(value) => {
                setVersionParam(value === "current" ? null : value);
                setSkip(0);
              }}
            >
              <SelectTrigger className="w-40" aria-label={t("versionLabel")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="current">
                  {t("currentVersion", { version: dataset.current_version_no })}
                </SelectItem>
                {versions
                  .filter((entry) => !entry.current)
                  .map((entry) => (
                    <SelectItem key={entry.id} value={String(entry.version_no)}>
                      v{entry.version_no} · {tDatasets(`versionKind.${entry.kind}`)}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>

            {historical && (
              <Badge variant="outline">
                {t("readingHistorical", { version: historical.version_no })}
              </Badge>
            )}
          </div>

          {historical && (
            <p className="rounded-card border-border bg-muted/40 text-muted-foreground border px-3 py-2 text-xs">
              {t("historicalHint", { version: historical.version_no })}
            </p>
          )}

          <DatasetTable
            dataset={dataset}
            rows={rows}
            loading={isLoading}
            asOf={version}
            error={error}
            onEdit={(row) => onRowAction(row, "edit")}
            onDelete={(row) => onRowAction(row, "delete")}
            onOperations={(row) => onOperations(row.record_key)}
          />

          <div className="text-muted-foreground flex items-center justify-between text-sm">
            <span>
              {t("pagingOf", { from: skip + 1, to: Math.min(skip + PAGE_SIZE, total), total })}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={skip === 0}
                onClick={() => setSkip((current) => Math.max(0, current - PAGE_SIZE))}
              >
                {t("previous")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={skip + PAGE_SIZE >= total}
                onClick={() => setSkip((current) => current + PAGE_SIZE)}
              >
                {t("next")}
              </Button>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="fields" className="data-[state=active]:block">
          <div className="divide-border rounded-card border-border divide-y border">
            {dataset.fields.map((field) => (
              <div
                key={field.name}
                className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm"
              >
                <span className="min-w-0 flex-1 truncate font-medium">{field.display_name}</span>
                <span className="text-muted-foreground font-mono text-xs">{field.name}</span>
                {/* `fieldType.*` lives in the `datasets` namespace, not in
                    `pages.datasets` - reading it from the page's own translator
                    resolved nothing and printed the key path. */}
                <Badge variant="outline">{tDatasets(`fieldType.${field.data_type}`)}</Badge>
                {field.is_primary_key && <Badge variant="secondary">{t("key")}</Badge>}
              </div>
            ))}
          </div>
          <div className="text-muted-foreground mt-3 flex flex-wrap gap-2 text-xs">
            <span>{t("keyFields", { fields: dataset.primary_key_fields.join(", ") })}</span>
          </div>
        </TabsContent>

        <TabsContent value="versions" className="data-[state=active]:block">
          <VersionPanel
            key={`${dataset.id}:${compareFrom ?? ""}`}
            dataset={dataset}
            fromVersion={compareFrom}
          />
        </TabsContent>

        <TabsContent value="relations" className="data-[state=active]:block">
          <RelationPanel dataset={dataset} siblings={siblings} />
        </TabsContent>

        <TabsContent value="operations" className="data-[state=active]:block">
          <OperationsPanel dataset={dataset} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
