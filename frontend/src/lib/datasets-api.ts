/**
 * Typed client for dataset knowledge bases, on `api-client.ts`.
 *
 * One function per route, all under the existing `/api/kb` proxy - a dataset is a
 * knowledge base, so its routes are already forwarded. Nothing here builds a
 * query string SQL could read: the backend takes a described query, not a
 * statement.
 */

import { apiClient } from "./api-client";
import type {
  CommitSheet,
  Dataset,
  DatasetBase,
  DatasetCompare,
  DatasetFieldInput,
  DatasetList,
  DatasetOperation,
  DatasetRelation,
  DatasetRowList,
  DatasetVersionList,
  ImportAccepted,
  ImportDraft,
  ImportPlan,
  ImportProgress,
  ImportResult,
  ImportStatus,
  RelationCandidate,
  WorkbookImport,
} from "@/types/datasets";

export function createDatasetBase(input: {
  name: string;
  description?: string | null;
  scope?: "personal" | "org";
}): Promise<DatasetBase> {
  return apiClient.post<DatasetBase>("/kb/datasets/bases", input);
}

export function listDatasets(kbId: string): Promise<DatasetList> {
  return apiClient.get<DatasetList>(`/kb/${kbId}/datasets`);
}

export function updateModel(
  datasetId: string,
  input: {
    fields: DatasetFieldInput[];
    metrics: Array<{
      name: string;
      agg: string;
      field_name?: string | null;
      display_name?: string | null;
    }>;
    note?: string | null;
  },
): Promise<Dataset> {
  return apiClient.put<Dataset>(`/kb/datasets/${datasetId}/model`, input);
}

export function archiveDataset(datasetId: string): Promise<void> {
  return apiClient.delete<void>(`/kb/datasets/${datasetId}`);
}

export function listRows(
  datasetId: string,
  query: { version?: number; search?: string; skip?: number; limit?: number } = {},
): Promise<DatasetRowList> {
  const params: Record<string, string> = {
    skip: String(query.skip ?? 0),
    limit: String(query.limit ?? 50),
  };
  if (query.version) params.version = String(query.version);
  if (query.search) params.search = query.search;
  return apiClient.get<DatasetRowList>(`/kb/datasets/${datasetId}/rows`, { params });
}

export function updateRow(
  datasetId: string,
  recordKey: string,
  input: { values: Record<string, unknown>; reason: string; expected_version?: number },
): Promise<{ version_no: number; record_key: string }> {
  return apiClient.patch(`/kb/datasets/${datasetId}/rows/${encodeURIComponent(recordKey)}`, input);
}

export function insertRow(
  datasetId: string,
  input: { values: Record<string, unknown>; reason: string },
): Promise<{ version_no: number }> {
  return apiClient.post(`/kb/datasets/${datasetId}/rows`, input);
}

export function deleteRow(
  datasetId: string,
  recordKey: string,
  input: { reason: string; expected_version?: number },
): Promise<{ version_no: number; record_key: string }> {
  const params: Record<string, string> = { reason: input.reason };
  if (input.expected_version) params.expected_version = String(input.expected_version);
  return apiClient.delete(`/kb/datasets/${datasetId}/rows/${encodeURIComponent(recordKey)}`, {
    params,
  });
}

export function restoreRow(
  datasetId: string,
  recordKey: string,
  input: { reason: string },
): Promise<{ version_no: number; record_key: string }> {
  return apiClient.post(
    `/kb/datasets/${datasetId}/rows/${encodeURIComponent(recordKey)}/restore`,
    input,
  );
}

export function listVersions(datasetId: string): Promise<DatasetVersionList> {
  return apiClient.get<DatasetVersionList>(`/kb/datasets/${datasetId}/versions`, {
    params: { limit: "100" },
  });
}

export function compareVersions(
  datasetId: string,
  fromVersion: number,
  toVersion: number,
  limit = 200,
): Promise<DatasetCompare> {
  return apiClient.get<DatasetCompare>(`/kb/datasets/${datasetId}/compare`, {
    params: {
      from_version: String(fromVersion),
      to_version: String(toVersion),
      limit: String(limit),
    },
  });
}

export function listRelations(
  datasetId: string,
): Promise<{ items: DatasetRelation[]; total: number }> {
  return apiClient.get(`/kb/datasets/${datasetId}/relations`);
}

export function relationCandidates(
  datasetId: string,
): Promise<{ items: RelationCandidate[]; total: number }> {
  return apiClient.get(`/kb/datasets/${datasetId}/relation-candidates`);
}

export function createRelation(
  datasetId: string,
  input: {
    target_dataset_id: string;
    name: string;
    cardinality: string;
    join_type?: string;
    local_fields: string[];
    foreign_fields: string[];
    status?: string;
    description?: string | null;
  },
): Promise<{ relation: DatasetRelation; warnings: string[] }> {
  return apiClient.post(`/kb/datasets/${datasetId}/relations`, input);
}

export function deleteRelation(datasetId: string, relationId: string): Promise<void> {
  return apiClient.delete<void>(`/kb/datasets/${datasetId}/relations/${relationId}`);
}

export function listOperations(
  datasetId: string,
): Promise<{ items: DatasetOperation[]; total: number }> {
  return apiClient.get(`/kb/datasets/${datasetId}/operations`, { params: { limit: "100" } });
}

export function uploadWorkbook(
  kbId: string,
  file: File,
  options?: { onProgress?: (fraction: number) => void },
): Promise<WorkbookImport> {
  return apiClient.upload<WorkbookImport>(`/kb/${kbId}/datasets/imports`, file, options);
}

export function commitImport(
  kbId: string,
  importId: string,
  input: { sheets: CommitSheet[]; note?: string | null },
): Promise<ImportAccepted> {
  return apiClient.post<ImportAccepted>(`/kb/${kbId}/datasets/imports/${importId}/commit`, input);
}

export function getImport(kbId: string, importId: string): Promise<ImportStatus> {
  return apiClient.get<ImportStatus>(`/kb/${kbId}/datasets/imports/${importId}`);
}

/** Keep the wizard's decisions; publishes nothing. */
export function saveImportDraft(
  kbId: string,
  importId: string,
  draft: ImportDraft,
): Promise<ImportPlan> {
  return apiClient.put<ImportPlan>(`/kb/${kbId}/datasets/imports/${importId}`, draft);
}

const SETTLED = new Set<ImportResult["status"]>(["committed", "partial", "failed"]);

export interface ImportWaitOptions {
  intervalMs?: number;
  timeoutMs?: number;
  onProgress?: (progress: ImportProgress) => void;
  signal?: AbortSignal;
  /** For tests: an interval that is not waited for, so polling is not slow. */
  sleep?: (ms: number) => Promise<void>;
}

/**
 * Poll an accepted import until its worker is done with it.
 *
 * The commit answers before the workbook is read, so the result the wizard
 * shows is the one this reads last. The wait has a ceiling on purpose: a job
 * whose worker died is closed by the scheduler, but a network that stops
 * answering should surface as an error rather than a spinner that never ends.
 */
export async function waitForImport(
  kbId: string,
  importId: string,
  options: ImportWaitOptions = {},
): Promise<ImportResult> {
  const {
    intervalMs = 1000,
    timeoutMs = 30 * 60 * 1000,
    onProgress,
    signal,
    sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
  } = options;
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    // i18n-exempt: a diagnostic this module throws, never copy - the hooks that
    // call it report the failure with a translated message of their own.
    if (signal?.aborted) throw new Error("The import was cancelled");
    const record = await getImport(kbId, importId);
    onProgress?.(record.progress);
    if (SETTLED.has(record.status as ImportResult["status"])) {
      return {
        status: record.status as ImportResult["status"],
        sheets: record.sheets,
        failures: record.progress.failed,
      };
    }
    // i18n-exempt: same - the hook owns the sentence the person reads.
    if (Date.now() > deadline) throw new Error("The import did not finish in time");
    await sleep(intervalMs);
  }
}
