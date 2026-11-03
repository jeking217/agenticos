import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useArchiveDataset,
  useCommitImport,
  useCreateDatasetBase,
  useDatasetBases,
  useDatasetRows,
  useDatasets,
  useDatasetVersions,
  useOperations,
  useRelationCandidates,
  useRelationMutations,
  useRelations,
  useRowMutations,
  useUpdateModel,
  useUploadWorkbook,
  useVersionCompare,
} from "./use-datasets";
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
import { apiClient } from "@/lib/api-client";

vi.mock("@/lib/datasets-api", () => ({
  archiveDataset: vi.fn(),
  commitImport: vi.fn(),
  compareVersions: vi.fn(),
  createDatasetBase: vi.fn(),
  createRelation: vi.fn(),
  deleteRelation: vi.fn(),
  deleteRow: vi.fn(),
  insertRow: vi.fn(),
  listDatasets: vi.fn(),
  listOperations: vi.fn(),
  listRelations: vi.fn(),
  listRows: vi.fn(),
  listVersions: vi.fn(),
  relationCandidates: vi.fn(),
  restoreRow: vi.fn(),
  updateModel: vi.fn(),
  updateRow: vi.fn(),
  uploadWorkbook: vi.fn(),
  waitForImport: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({ apiClient: { get: vi.fn() } }));

// A key-returning translator, the convention the rest of the suite uses: the
// assertions below name a message key rather than its English.
vi.mock("next-intl", async () => ({
  useTranslations: (await import("@/test-utils/intl")).keyTranslations(),
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
const toastWarning = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
    warning: (...args: unknown[]) => toastWarning(...args),
  },
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiClient.get).mockResolvedValue({ items: [] });
  vi.mocked(listDatasets).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(listRows).mockResolvedValue({ items: [], total: 0, version_no: 1, columns: [] });
  vi.mocked(listVersions).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(listRelations).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(relationCandidates).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(listOperations).mockResolvedValue({ items: [], total: 0 });
});

describe("reading a base", () => {
  it("keeps only the bases that hold rows, and shows nothing while it asks", async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      items: [
        { id: "kb-1", name: "Sales", description: null, scope: "org", kb_type: "dataset" },
        { id: "kb-2", name: "Handbook", description: null, scope: "org", kb_type: "document" },
      ],
    });

    const { result } = renderHook(() => useDatasetBases(), { wrapper });

    // Before the answer arrives the list is empty rather than undefined: every
    // caller maps over it.
    expect(result.current.bases).toEqual([]);

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(apiClient.get).toHaveBeenCalledWith("/kb");
    expect(result.current.bases.map((base) => base.id)).toEqual(["kb-1"]);
  });

  it("reads a base's datasets", async () => {
    vi.mocked(listDatasets).mockResolvedValue({
      items: [{ id: "ds-1", name: "Orders" } as never],
      total: 1,
    });

    const { result } = renderHook(() => useDatasets("kb-1"), { wrapper });

    await waitFor(() => expect(result.current.datasets).toHaveLength(1));
    expect(listDatasets).toHaveBeenCalledWith("kb-1");
  });

  it("does not ask for a base it was not given", async () => {
    const { result } = renderHook(() => useDatasets(""), { wrapper });

    await waitFor(() => expect(result.current.fetchStatus).toBe("idle"));
    expect(listDatasets).not.toHaveBeenCalled();
  });
});

describe("reading a dataset", () => {
  it("pages the rows, sending a filter and a version only when there is one", async () => {
    vi.mocked(listRows).mockResolvedValue({
      items: [
        {
          record_key: "a",
          record_version: 2,
          valid_from: 2,
          valid_to: null,
          is_deleted: false,
          values: {},
        },
      ],
      total: 1,
      version_no: 2,
      columns: [],
    });

    const { result } = renderHook(
      () => useDatasetRows("ds-1", { version: null, search: "", skip: 0 }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.total).toBe(1));
    expect(listRows).toHaveBeenCalledWith("ds-1", {
      version: undefined,
      search: undefined,
      skip: 0,
      limit: 50,
    });
    expect(result.current.rows).toHaveLength(1);

    const historical = renderHook(
      () => useDatasetRows("ds-1", { version: 2, search: "北京", skip: 50, limit: 25 }),
      { wrapper },
    );

    await waitFor(() =>
      expect(listRows).toHaveBeenLastCalledWith("ds-1", {
        version: 2,
        search: "北京",
        skip: 50,
        limit: 25,
      }),
    );
    expect(historical.result.current.rows).toEqual([]);
  });

  it("reads versions, relations, operations and the joins worth suggesting", async () => {
    const versions = renderHook(() => useDatasetVersions("ds-1"), { wrapper });
    await waitFor(() => expect(versions.result.current.versions).toEqual([]));

    const relations = renderHook(() => useRelations("ds-1"), { wrapper });
    await waitFor(() => expect(relations.result.current.relations).toEqual([]));

    const operations = renderHook(() => useOperations("ds-1"), { wrapper });
    await waitFor(() => expect(operations.result.current.operations).toEqual([]));

    const candidates = renderHook(() => useRelationCandidates("ds-1", true), { wrapper });
    await waitFor(() => expect(candidates.result.current.candidates).toEqual([]));
    expect(relationCandidates).toHaveBeenCalledWith("ds-1");
  });

  it("suggests joins only when the panel is open", async () => {
    const { result } = renderHook(() => useRelationCandidates("ds-1", false), { wrapper });

    await waitFor(() => expect(result.current.fetchStatus).toBe("idle"));
    expect(relationCandidates).not.toHaveBeenCalled();
  });

  it("compares two versions, and only two different ones", async () => {
    vi.mocked(compareVersions).mockResolvedValue({
      from_version: 1,
      to_version: 2,
      added: 1,
      changed: 0,
      removed: 0,
      missing: 0,
      unchanged: 0,
      rows: [],
      truncated: false,
    });

    const same = renderHook(() => useVersionCompare("ds-1", 2, 2), { wrapper });
    await waitFor(() => expect(same.result.current.fetchStatus).toBe("idle"));
    expect(compareVersions).not.toHaveBeenCalled();

    const nothing = renderHook(() => useVersionCompare("ds-1", null, null), { wrapper });
    await waitFor(() => expect(nothing.result.current.fetchStatus).toBe("idle"));

    const { result } = renderHook(() => useVersionCompare("ds-1", 1, 2), { wrapper });
    await waitFor(() => expect(result.current.compare?.added).toBe(1));
    expect(compareVersions).toHaveBeenCalledWith("ds-1", 1, 2);
  });
});

describe("changing a base", () => {
  it("creates a base and names it in the toast", async () => {
    vi.mocked(createDatasetBase).mockResolvedValue({
      id: "kb-1",
      name: "Sales",
    } as never);
    const { result } = renderHook(() => useCreateDatasetBase(), { wrapper });

    await act(() => result.current.mutateAsync({ name: "Sales", scope: "org" }));

    expect(createDatasetBase).toHaveBeenCalledWith({ name: "Sales", scope: "org" });
    expect(toastSuccess).toHaveBeenCalledWith("baseCreated");
  });

  it("says the base could not be created", async () => {
    vi.mocked(createDatasetBase).mockRejectedValueOnce(new Error("nope"));
    const { result } = renderHook(() => useCreateDatasetBase(), { wrapper });

    await act(() => result.current.mutateAsync({ name: "Sales" }).catch(() => undefined));

    expect(toastError).toHaveBeenCalledWith("baseCreateFailed");
  });

  it("uploads a workbook without claiming anything was published", async () => {
    vi.mocked(uploadWorkbook).mockResolvedValueOnce({ id: "imp-1", sheets: [] } as never);
    const file = new File(["x"], "orders.xlsx");
    const onProgress = vi.fn();
    const { result } = renderHook(() => useUploadWorkbook("kb-1"), { wrapper });

    await act(() => result.current.mutateAsync({ file, onProgress }));

    // The fraction the wizard's progress bar reads travels with the request, so
    // a 40 MB workbook is not a spinner with no idea how far along it is.
    expect(uploadWorkbook).toHaveBeenCalledWith("kb-1", file, { onProgress });
    // The upload only stages the file; publishing is the commit's job, so nothing
    // is announced here.
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});

describe("publishing an import", () => {
  const sheets = [{ sheet_name: "订单明细", dataset_id: "ds-1" }];
  const accepted = {
    import_id: "imp-1",
    status: "queued" as const,
    progress: {
      phase: "queued" as const,
      total: 1,
      done: 0,
      failed: 0,
      current_sheet: null,
      percent: 0,
    },
  };

  it("reports a clean publish as the number of sheets", async () => {
    vi.mocked(commitImport).mockResolvedValueOnce(accepted);
    vi.mocked(waitForImport).mockResolvedValueOnce({
      status: "committed",
      sheets: [{ sheet_name: "订单明细", status: "committed" }],
      failures: 0,
    });
    const { result } = renderHook(() => useCommitImport("kb-1"), { wrapper });

    await act(() => result.current.mutateAsync({ importId: "imp-1", sheets }));

    expect(commitImport).toHaveBeenCalledWith("kb-1", "imp-1", { sheets, note: undefined });
    // The worker publishes it; the hook waits on the answer it accepted.
    expect(waitForImport).toHaveBeenCalledWith("kb-1", "imp-1", { onProgress: undefined });
    expect(toastSuccess).toHaveBeenCalledWith("importDone");
  });

  it("reports a partial publish as a warning that counts the failures", async () => {
    vi.mocked(commitImport).mockResolvedValueOnce(accepted);
    vi.mocked(waitForImport).mockResolvedValueOnce({
      status: "partial",
      sheets: [],
      failures: 1,
    });
    const { result } = renderHook(() => useCommitImport("kb-1"), { wrapper });

    await act(() => result.current.mutateAsync({ importId: "imp-1", sheets, note: "march" }));

    expect(toastWarning).toHaveBeenCalledWith("importPartial");
  });

  it("reports a publish where nothing landed as a failure", async () => {
    vi.mocked(commitImport).mockResolvedValueOnce(accepted);
    vi.mocked(waitForImport).mockResolvedValueOnce({ status: "failed", sheets: [], failures: 1 });
    const { result } = renderHook(() => useCommitImport("kb-1"), { wrapper });

    await act(() => result.current.mutateAsync({ importId: "imp-1", sheets }));

    expect(toastError).toHaveBeenCalledWith("importFailed");
  });

  it("passes the sheet in flight to whoever draws the bar", async () => {
    vi.mocked(commitImport).mockResolvedValueOnce(accepted);
    vi.mocked(waitForImport).mockResolvedValueOnce({
      status: "committed",
      sheets: [{ sheet_name: "订单明细", status: "committed" }],
      failures: 0,
    });
    const onProgress = vi.fn();
    const { result } = renderHook(() => useCommitImport("kb-1"), { wrapper });

    await act(() => result.current.mutateAsync({ importId: "imp-1", sheets, onProgress }));

    expect(waitForImport).toHaveBeenCalledWith("kb-1", "imp-1", { onProgress });
  });

  it("reports a publish that never got an answer", async () => {
    vi.mocked(commitImport).mockRejectedValueOnce(new Error("timeout"));
    const { result } = renderHook(() => useCommitImport("kb-1"), { wrapper });

    await act(() =>
      result.current.mutateAsync({ importId: "imp-1", sheets }).catch(() => undefined),
    );

    expect(toastError).toHaveBeenCalledWith("importFailed");
  });
});

describe("changing a model or a row", () => {
  it("saves the model with no metrics the console cannot author", async () => {
    vi.mocked(updateModel).mockResolvedValueOnce({ id: "ds-1" } as never);
    const { result } = renderHook(() => useUpdateModel("ds-1"), { wrapper });

    await act(() => result.current.mutateAsync({ fields: [], note: "console" }));

    expect(updateModel).toHaveBeenCalledWith("ds-1", {
      fields: [],
      metrics: [],
      note: "console",
    });
    expect(toastSuccess).toHaveBeenCalledWith("modelSaved");
  });

  it("says the model could not be saved", async () => {
    vi.mocked(updateModel).mockRejectedValueOnce(new Error("frozen"));
    const { result } = renderHook(() => useUpdateModel("ds-1"), { wrapper });

    await act(() => result.current.mutateAsync({ fields: [] }).catch(() => undefined));

    expect(toastError).toHaveBeenCalledWith("modelSaveFailed");
  });

  it("edits, removes, restores and adds a row, each with its own toast", async () => {
    vi.mocked(updateRow).mockResolvedValueOnce({ version_no: 2, record_key: "a" });
    vi.mocked(deleteRow).mockResolvedValueOnce({ version_no: 3, record_key: "a" });
    vi.mocked(restoreRow).mockResolvedValueOnce({ version_no: 4, record_key: "a" });
    vi.mocked(insertRow).mockResolvedValueOnce({ version_no: 5 });

    const { result } = renderHook(() => useRowMutations("ds-1"), { wrapper });

    await act(() =>
      result.current.update.mutateAsync({ recordKey: "a", values: { 金额: 1 }, reason: "fix" }),
    );
    expect(updateRow).toHaveBeenCalledWith("ds-1", "a", { values: { 金额: 1 }, reason: "fix" });
    expect(toastSuccess).toHaveBeenCalledWith("rowSaved");

    await act(() => result.current.remove.mutateAsync({ recordKey: "a", reason: "wrong" }));
    expect(deleteRow).toHaveBeenCalledWith("ds-1", "a", { reason: "wrong" });
    expect(toastSuccess).toHaveBeenCalledWith("rowDeleted");

    await act(() => result.current.restore.mutateAsync({ recordKey: "a", reason: "back" }));
    expect(restoreRow).toHaveBeenCalledWith("ds-1", "a", { reason: "back" });
    expect(toastSuccess).toHaveBeenCalledWith("rowRestored");

    await act(() => result.current.insert.mutateAsync({ values: { 金额: 2 }, reason: "new" }));
    expect(insertRow).toHaveBeenCalledWith("ds-1", { values: { 金额: 2 }, reason: "new" });
    expect(toastSuccess).toHaveBeenCalledWith("rowAdded");
  });

  it("names the same failure whichever row action it was", async () => {
    vi.mocked(updateRow).mockRejectedValueOnce(new Error("stale"));
    const { result } = renderHook(() => useRowMutations("ds-1"), { wrapper });

    await act(() =>
      result.current.update
        .mutateAsync({ recordKey: "a", values: {}, reason: "fix" })
        .catch(() => undefined),
    );

    expect(toastError).toHaveBeenCalledWith("rowWriteFailed");
  });
});

describe("relations and archiving", () => {
  it("confirms a join and removes one", async () => {
    vi.mocked(createRelation).mockResolvedValueOnce({
      relation: { id: "rel-1" },
      warnings: [],
    } as never);
    vi.mocked(deleteRelation).mockResolvedValueOnce(undefined);

    const { result } = renderHook(() => useRelationMutations("ds-1"), { wrapper });

    await act(() =>
      result.current.create.mutateAsync({
        target_dataset_id: "ds-2",
        name: "orders → customers",
        cardinality: "many_to_one",
        local_fields: ["客户编号"],
        foreign_fields: ["编号"],
      }),
    );
    expect(createRelation).toHaveBeenCalledWith("ds-1", {
      target_dataset_id: "ds-2",
      name: "orders → customers",
      cardinality: "many_to_one",
      local_fields: ["客户编号"],
      foreign_fields: ["编号"],
    });
    expect(toastSuccess).toHaveBeenCalledWith("relationSaved");

    await act(() => result.current.remove.mutateAsync("rel-1"));
    expect(deleteRelation).toHaveBeenCalledWith("ds-1", "rel-1");
    expect(toastSuccess).toHaveBeenCalledWith("relationRemoved");
  });

  it("says the join could not be saved, whichever half failed", async () => {
    vi.mocked(createRelation).mockRejectedValueOnce(new Error("duplicate"));
    vi.mocked(deleteRelation).mockRejectedValueOnce(new Error("gone"));

    const { result } = renderHook(() => useRelationMutations("ds-1"), { wrapper });

    await act(() =>
      result.current.create
        .mutateAsync({
          target_dataset_id: "ds-2",
          name: "x",
          cardinality: "many_to_one",
          local_fields: [],
          foreign_fields: [],
        })
        .catch(() => undefined),
    );
    await act(() => result.current.remove.mutateAsync("rel-1").catch(() => undefined));

    expect(toastError).toHaveBeenCalledTimes(2);
    expect(toastError).toHaveBeenCalledWith("relationFailed");
  });

  it("archives a dataset and says so", async () => {
    vi.mocked(archiveDataset).mockResolvedValueOnce(undefined);
    const { result } = renderHook(() => useArchiveDataset("kb-1"), { wrapper });

    await act(() => result.current.mutateAsync("ds-1"));

    expect(archiveDataset).toHaveBeenCalledWith("ds-1");
    expect(toastSuccess).toHaveBeenCalledWith("datasetArchived");
  });

  it("says the dataset could not be archived", async () => {
    vi.mocked(archiveDataset).mockRejectedValueOnce(new Error("pinned"));
    const { result } = renderHook(() => useArchiveDataset("kb-1"), { wrapper });

    await act(() => result.current.mutateAsync("ds-1").catch(() => undefined));

    expect(toastError).toHaveBeenCalledWith("datasetArchiveFailed");
  });
});
