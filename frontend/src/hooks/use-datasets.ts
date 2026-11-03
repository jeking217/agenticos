"use client";

/**
 * Server state for datasets, through React Query.
 *
 * A publish changes rows, versions and operations at once, so those mutations
 * invalidate the dataset prefix rather than one key: the point of a version is
 * that every view of the dataset agrees about it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useTranslations } from "next-intl";

import { apiClient } from "@/lib/api-client";
import {
  archiveDataset,
  commitImport,
  compareVersions,
  createDatasetBase,
  createRelation,
  deleteRelation,
  deleteRow,
  insertRow,
  listDatasets,
  listOperations,
  listRelations,
  listRows,
  listVersions,
  relationCandidates,
  restoreRow,
  updateModel,
  updateRow,
  uploadWorkbook,
  waitForImport,
} from "@/lib/datasets-api";
import { qk } from "@/lib/query-keys";
import type { CommitSheet, DatasetBase, DatasetFieldInput, ImportProgress } from "@/types/datasets";

export interface DatasetBaseSummary {
  id: string;
  name: string;
  description: string | null;
  scope: "personal" | "org" | "app";
  kb_type: "document" | "dataset";
  created_at: string;
}

export function useDatasetBases() {
  const query = useQuery({
    queryKey: qk.datasets.bases(),
    queryFn: async () => {
      const response = await apiClient.get<{ items: DatasetBaseSummary[] }>("/kb");
      return response.items.filter((base) => base.kb_type === "dataset");
    },
  });
  return { bases: query.data ?? [], ...query };
}

export function useDatasets(kbId: string) {
  const query = useQuery({
    queryKey: qk.datasets.list(kbId),
    queryFn: async () => (await listDatasets(kbId)).items,
    enabled: Boolean(kbId),
  });
  return { datasets: query.data ?? [], ...query };
}

export function useDatasetRows(
  datasetId: string,
  options: { version: number | null; search: string; skip: number; limit?: number },
) {
  const query = useQuery({
    queryKey: qk.datasets.rows(datasetId, options.version, options.search, options.skip),
    queryFn: () =>
      listRows(datasetId, {
        version: options.version ?? undefined,
        search: options.search || undefined,
        skip: options.skip,
        limit: options.limit ?? 50,
      }),
    enabled: Boolean(datasetId),
    // A version's rows never change: the past is append-only, so a page of it
    // stays valid until the whole dataset is invalidated by a publish.
    staleTime: 60_000,
  });
  return { rows: query.data?.items ?? [], total: query.data?.total ?? 0, ...query };
}

export function useDatasetVersions(datasetId: string) {
  const query = useQuery({
    queryKey: qk.datasets.versions(datasetId),
    queryFn: () => listVersions(datasetId),
    enabled: Boolean(datasetId),
  });
  // `total` travels with the page: a list that came back full may have stopped
  // short of the version somebody is asking about (#T23).
  return {
    versions: query.data?.items ?? [],
    total: query.data?.total ?? 0,
    ...query,
  };
}

export function useVersionCompare(datasetId: string, from: number | null, to: number | null) {
  const query = useQuery({
    queryKey: qk.datasets.compare(datasetId, from ?? 0, to ?? 0),
    queryFn: () => compareVersions(datasetId, from as number, to as number),
    enabled: Boolean(datasetId) && from !== null && to !== null && from !== to,
  });
  return { compare: query.data, ...query };
}

export function useRelations(datasetId: string) {
  const query = useQuery({
    queryKey: qk.datasets.relations(datasetId),
    queryFn: async () => (await listRelations(datasetId)).items,
    enabled: Boolean(datasetId),
  });
  return { relations: query.data ?? [], ...query };
}

export function useRelationCandidates(datasetId: string, enabled: boolean) {
  const query = useQuery({
    queryKey: qk.datasets.candidates(datasetId),
    queryFn: async () => (await relationCandidates(datasetId)).items,
    enabled: Boolean(datasetId) && enabled,
  });
  return { candidates: query.data ?? [], ...query };
}

export function useOperations(datasetId: string) {
  const query = useQuery({
    queryKey: qk.datasets.operations(datasetId),
    queryFn: async () => (await listOperations(datasetId)).items,
    enabled: Boolean(datasetId),
  });
  return { operations: query.data ?? [], ...query };
}

export function useCreateDatasetBase() {
  const queryClient = useQueryClient();
  const t = useTranslations("datasets");
  return useMutation({
    mutationFn: (input: {
      name: string;
      description?: string | null;
      scope?: "personal" | "org";
    }) => createDatasetBase(input),
    onSuccess: (base: DatasetBase) => {
      queryClient.invalidateQueries({ queryKey: qk.datasets.bases() });
      queryClient.invalidateQueries({ queryKey: qk.kb.list() });
      toast.success(t("baseCreated", { name: base.name }));
    },
    onError: () => toast.error(t("baseCreateFailed")),
  });
}

export function useUploadWorkbook(kbId: string) {
  return useMutation({
    // The wizard shows how much of a 40 MB workbook has gone, so the fraction is
    // part of the call rather than a second, separately-timed request.
    mutationFn: (input: { file: File; onProgress?: (fraction: number) => void }) =>
      uploadWorkbook(kbId, input.file, { onProgress: input.onProgress }),
  });
}

export function useCommitImport(kbId: string) {
  const queryClient = useQueryClient();
  const t = useTranslations("datasets");
  return useMutation({
    // Accept, then poll: the publish happens on a worker, so the answer to the
    // request is "queued" and the result is whatever the last poll reads.
    mutationFn: async (input: {
      importId: string;
      sheets: CommitSheet[];
      note?: string;
      onProgress?: (progress: ImportProgress) => void;
    }) => {
      const accepted = await commitImport(kbId, input.importId, {
        sheets: input.sheets,
        note: input.note,
      });
      return waitForImport(kbId, accepted.import_id, { onProgress: input.onProgress });
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: qk.datasets.list(kbId) });
      queryClient.invalidateQueries({ queryKey: qk.datasets.all() });
      if (result.status === "committed") {
        toast.success(t("importDone", { count: result.sheets.length }));
      } else if (result.status === "partial") {
        toast.warning(t("importPartial", { count: result.failures }));
      } else {
        toast.error(t("importFailed"));
      }
    },
    onError: () => toast.error(t("importFailed")),
  });
}

export function useUpdateModel(datasetId: string) {
  const queryClient = useQueryClient();
  const t = useTranslations("datasets");
  return useMutation({
    mutationFn: (input: { fields: DatasetFieldInput[]; note?: string }) =>
      updateModel(datasetId, { fields: input.fields, metrics: [], note: input.note }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.datasets.dataset(datasetId) });
      toast.success(t("modelSaved"));
    },
    onError: () => toast.error(t("modelSaveFailed")),
  });
}

export function useRowMutations(datasetId: string) {
  const queryClient = useQueryClient();
  const t = useTranslations("datasets");
  const refresh = () => queryClient.invalidateQueries({ queryKey: qk.datasets.all() });
  const failure = () => toast.error(t("rowWriteFailed"));

  const update = useMutation({
    mutationFn: (input: { recordKey: string; values: Record<string, unknown>; reason: string }) =>
      updateRow(datasetId, input.recordKey, { values: input.values, reason: input.reason }),
    onSuccess: () => {
      refresh();
      toast.success(t("rowSaved"));
    },
    onError: failure,
  });

  const remove = useMutation({
    mutationFn: (input: { recordKey: string; reason: string }) =>
      deleteRow(datasetId, input.recordKey, { reason: input.reason }),
    onSuccess: () => {
      refresh();
      toast.success(t("rowDeleted"));
    },
    onError: failure,
  });

  const restore = useMutation({
    mutationFn: (input: { recordKey: string; reason: string }) =>
      restoreRow(datasetId, input.recordKey, { reason: input.reason }),
    onSuccess: () => {
      refresh();
      toast.success(t("rowRestored"));
    },
    onError: failure,
  });

  const insert = useMutation({
    mutationFn: (input: { values: Record<string, unknown>; reason: string }) =>
      insertRow(datasetId, input),
    onSuccess: () => {
      refresh();
      toast.success(t("rowAdded"));
    },
    onError: failure,
  });

  return { update, remove, restore, insert };
}

export function useRelationMutations(datasetId: string) {
  const queryClient = useQueryClient();
  const t = useTranslations("datasets");
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: qk.datasets.relations(datasetId) });
    queryClient.invalidateQueries({ queryKey: qk.datasets.candidates(datasetId) });
  };

  const create = useMutation({
    mutationFn: (input: {
      target_dataset_id: string;
      name: string;
      cardinality: string;
      local_fields: string[];
      foreign_fields: string[];
      status?: string;
    }) => createRelation(datasetId, input),
    onSuccess: () => {
      invalidate();
      toast.success(t("relationSaved"));
    },
    onError: () => toast.error(t("relationFailed")),
  });

  const remove = useMutation({
    mutationFn: (relationId: string) => deleteRelation(datasetId, relationId),
    onSuccess: () => {
      invalidate();
      toast.success(t("relationRemoved"));
    },
    onError: () => toast.error(t("relationFailed")),
  });

  return { create, remove };
}

export function useArchiveDataset(kbId: string) {
  const queryClient = useQueryClient();
  const t = useTranslations("datasets");
  return useMutation({
    mutationFn: (datasetId: string) => archiveDataset(datasetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.datasets.list(kbId) });
      toast.success(t("datasetArchived"));
    },
    onError: () => toast.error(t("datasetArchiveFailed")),
  });
}
