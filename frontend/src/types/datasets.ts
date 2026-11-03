/**
 * Excel datasets: the wire shapes the backend answers with.
 *
 * A dataset is a knowledge base of a second kind. These types describe the model
 * (what a column means), the rows (what a version says), and the operations that
 * changed them - the three things the UI shows side by side.
 */

export type DatasetFieldType = "text" | "integer" | "numeric" | "date" | "timestamp" | "boolean";
export type MetricAgg = "sum" | "avg" | "min" | "max" | "count" | "count_distinct";
export type VersionKind = "import" | "maintenance" | "model" | "restore";
export type RelationCardinality = "1:1" | "1:n" | "n:1";
export type RelationStatus = "confirmed" | "candidate" | "disabled";
export type RowChangeKind = "added" | "changed" | "removed" | "missing";

export interface DatasetField {
  name: string;
  display_name: string;
  data_type: DatasetFieldType;
  ordinal: number;
  is_nullable: boolean;
  is_primary_key: boolean;
  synonyms: string[];
  format_hint: string | null;
  description: string | null;
}

export interface DatasetFieldInput {
  name: string;
  display_name?: string | null;
  data_type: DatasetFieldType;
  is_nullable?: boolean;
  is_primary_key?: boolean;
  synonyms?: string[];
  format_hint?: string | null;
  description?: string | null;
}

export interface DatasetMetric {
  name: string;
  display_name: string;
  agg: MetricAgg;
  field_name: string | null;
  format_hint: string | null;
  description: string | null;
}

export interface Dataset {
  id: string;
  knowledge_base_id: string;
  organization_id: string;
  name: string;
  sheet_name: string | null;
  source_filename: string | null;
  primary_key_fields: string[];
  current_version_no: number;
  model_version: number;
  storage_mode: "pooled" | "dedicated" | "archived";
  row_count: number;
  revision: number;
  fields: DatasetField[];
  metrics: DatasetMetric[];
  last_import_at: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface DatasetList {
  items: Dataset[];
  total: number;
}

export interface DatasetVersion {
  id: string;
  dataset_id: string;
  version_no: number;
  kind: VersionKind;
  source_filename: string | null;
  sheet_name: string | null;
  rows_added: number;
  rows_changed: number;
  rows_removed: number;
  rows_unchanged: number;
  note: string | null;
  published_at: string;
  published_by: string | null;
  stats: Record<string, unknown>;
  pinned?: boolean;
  current?: boolean;
}

export interface DatasetVersionList {
  items: DatasetVersion[];
  total: number;
}

export interface DatasetRow {
  record_key: string;
  record_version: number;
  valid_from: number;
  valid_to: number | null;
  is_deleted: boolean;
  values: Record<string, unknown>;
}

export interface DatasetRowList {
  items: DatasetRow[];
  total: number;
  version_no: number;
  columns: DatasetField[];
}

export interface DatasetRelation {
  id: string;
  dataset_id: string;
  target_dataset_id: string;
  target_dataset_name: string | null;
  name: string;
  cardinality: RelationCardinality;
  join_type: "inner" | "left";
  local_fields: string[];
  foreign_fields: string[];
  status: RelationStatus;
  confidence: number | null;
  description: string | null;
}

export interface RelationCandidate {
  target_dataset_id: string;
  target_dataset_name: string;
  name: string;
  local_fields: string[];
  foreign_fields: string[];
  cardinality: RelationCardinality;
  confidence: number;
  overlap: number;
  already_exists: boolean;
}

export interface DatasetOperation {
  id: string;
  version_no: number;
  operation: "insert" | "update" | "delete" | "restore" | "import" | "model";
  record_key: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  actor_user_id: string | null;
  created_at: string;
}

export interface SheetFieldProposal {
  name: string;
  display_name: string;
  data_type: DatasetFieldType;
  is_nullable: boolean;
  is_primary_key: boolean;
  distinct_ratio: number;
  samples: string[];
}

export interface SheetProposal {
  sheet_name: string;
  kind: "data" | "summary" | "empty";
  header_row: number | null;
  row_count: number;
  column_count: number;
  fields: SheetFieldProposal[];
  preview: Record<string, unknown>[];
  warnings: string[];
  merged_cells: number;
}

export interface WorkbookImport {
  id: string;
  knowledge_base_id: string;
  status: "draft" | "preview" | "queued" | "running" | "committed" | "partial" | "failed";
  filename: string;
  file_size: number;
  sheet_count: number;
  sheets: SheetProposal[];
  summary: Record<string, unknown>;
  error: string | null;
  committed_at: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface CommitSheet {
  sheet_name: string;
  dataset_id?: string;
  dataset_name?: string;
  fields?: DatasetFieldInput[];
  primary_key_fields?: string[];
}

export interface ImportSheetResult {
  sheet_name: string;
  status: "committed" | "failed";
  dataset_id?: string;
  dataset_name?: string;
  version_no?: number;
  published?: boolean;
  rows_added?: number;
  rows_changed?: number;
  rows_unchanged?: number;
  /** Rows with no identity, set aside because the sheet was told to skip them. */
  rows_withheld?: number;
  warnings?: string[];
  error?: string;
}

export interface ImportResult {
  status: "committed" | "partial" | "failed";
  sheets: ImportSheetResult[];
  failures: number;
}

/** How far a publish got. `done` counts a failed sheet: it is settled, not running. */
export interface ImportProgress {
  phase: "queued" | "running" | "done";
  total: number;
  done: number;
  failed: number;
  current_sheet: string | null;
  percent: number;
}

/** The answer to a commit: accepted, and not yet published. */
export interface ImportAccepted {
  import_id: string;
  status: WorkbookImport["status"];
  progress: ImportProgress;
}

/** What the poller reads while the worker publishes. */
export interface ImportStatus {
  id: string;
  knowledge_base_id: string;
  status: "queued" | "running" | "committed" | "partial" | "failed";
  filename: string;
  sheet_count: number;
  progress: ImportProgress;
  sheets: ImportSheetResult[];
  plan: ImportPlan;
  error: string | null;
  committed_at: string | null;
}

export type ImportStep = "sheets" | "model" | "validate" | "publish";

/** The decisions saved at a step, so a closed dialog resumes where it was. */
export interface ImportDraft {
  step: Exclude<ImportStep, "publish">;
  sheets: CommitSheet[];
  note?: string | null;
}

/** The parsed workbook and the decisions saved against it. */
export interface ImportPlan {
  filename: string;
  step: ImportStep;
  sheets: SheetProposal[];
  choices: ImportDraft | null;
}

export interface QueryColumn {
  name: string;
  data_type: string;
}

export interface DatasetQueryResult {
  columns: QueryColumn[];
  rows: Record<string, unknown>[];
  row_count: number;
  truncated: boolean;
  version_vector: Record<string, number>;
  sql: string;
  params: Record<string, string>;
  warnings: string[];
}

export interface RowChange {
  record_key: string;
  change: RowChangeKind;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  changed_fields: string[];
}

export interface DatasetCompare {
  from_version: number;
  to_version: number;
  added: number;
  changed: number;
  removed: number;
  missing: number;
  unchanged: number;
  rows: RowChange[];
  truncated: boolean;
}

/** A knowledge base that holds datasets, as the create dialog receives it back. */
export interface DatasetBase {
  id: string;
  name: string;
  description: string | null;
  scope: "personal" | "org";
  kb_type: "dataset";
}
