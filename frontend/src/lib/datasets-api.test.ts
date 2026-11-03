import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Every dataset route, called the way the pages call it.
 *
 * The client is one line per route, and that is the point of testing it: the
 * path and the payload are the contract the backend reads, and a typo in either
 * is a 404 or a 422 nobody sees until a person opens the tab. `apiClient` is
 * mocked because what it does with a request - the cookie, the refresh, the
 * error mapping - is `api-client.test.ts`'s subject, not this file's.
 */
const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());
const patch = vi.hoisted(() => vi.fn());
const del = vi.hoisted(() => vi.fn());
const upload = vi.hoisted(() => vi.fn());

vi.mock("./api-client", () => ({
  apiClient: { get, post, put, patch, delete: del, upload },
}));

import {
  analyzeImport,
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
  saveImportDraft,
  updateModel,
  updateRow,
  uploadWorkbook,
  waitForImport,
} from "./datasets-api";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("bases and datasets", () => {
  it("creates a base under the dataset root and lists a base's datasets", async () => {
    post.mockResolvedValueOnce({ id: "kb-1", name: "Sales" });
    get.mockResolvedValueOnce({ items: [], total: 0 });

    await expect(
      createDatasetBase({ name: "Sales", description: null, scope: "org" }),
    ).resolves.toEqual({ id: "kb-1", name: "Sales" });
    expect(post).toHaveBeenCalledWith("/kb/datasets/bases", {
      name: "Sales",
      description: null,
      scope: "org",
    });

    await listDatasets("kb-1");
    expect(get).toHaveBeenCalledWith("/kb/kb-1/datasets");
  });

  it("sends the base's reasoning model, so import advice has something to answer with", async () => {
    post.mockResolvedValueOnce({ id: "kb-1", name: "Sales" });

    await createDatasetBase({
      name: "Sales",
      description: null,
      scope: "personal",
      reasoning_provider: "openai",
      reasoning_model: "gpt-5-mini",
      reasoning_secret_id: "secret-1",
      reasoning_base_url: null,
    });

    // All four travel together: the backend rejects half a model, and a blank
    // string would read as "chosen" rather than "not chosen".
    expect(post).toHaveBeenCalledWith("/kb/datasets/bases", {
      name: "Sales",
      description: null,
      scope: "personal",
      reasoning_provider: "openai",
      reasoning_model: "gpt-5-mini",
      reasoning_secret_id: "secret-1",
      reasoning_base_url: null,
    });
  });

  it("sends the model as the form posted it - fields, metrics and the note", async () => {
    put.mockResolvedValueOnce({ id: "ds-1" });

    await updateModel("ds-1", {
      fields: [{ name: "订单号", data_type: "text", is_primary_key: true }],
      metrics: [],
      note: "fixed a type",
    });

    expect(put).toHaveBeenCalledWith("/kb/datasets/ds-1/model", {
      fields: [{ name: "订单号", data_type: "text", is_primary_key: true }],
      metrics: [],
      note: "fixed a type",
    });
  });

  it("archives a dataset with a delete on the dataset itself", async () => {
    del.mockResolvedValueOnce(undefined);

    await archiveDataset("ds-1");

    expect(del).toHaveBeenCalledWith("/kb/datasets/ds-1");
  });
});

describe("rows", () => {
  it("pages the current version with the defaults, and sends a filter only when asked", async () => {
    get.mockResolvedValue({ items: [], total: 0 });

    await listRows("ds-1");
    expect(get).toHaveBeenLastCalledWith("/kb/datasets/ds-1/rows", {
      params: { skip: "0", limit: "50" },
    });

    await listRows("ds-1", { version: 3, search: "北京", skip: 100, limit: 25 });
    expect(get).toHaveBeenLastCalledWith("/kb/datasets/ds-1/rows", {
      params: { skip: "100", limit: "25", version: "3", search: "北京" },
    });
  });

  it("edits, adds, removes and restores one record", async () => {
    patch.mockResolvedValueOnce({ version_no: 8, record_key: "a" });
    post.mockResolvedValue({ version_no: 9 });
    del.mockResolvedValue({ version_no: 10, record_key: "a" });

    await updateRow("ds-1", "a/b", { values: { 金额: 100 }, reason: "price fix" });
    expect(patch).toHaveBeenCalledWith("/kb/datasets/ds-1/rows/a%2Fb", {
      values: { 金额: 100 },
      reason: "price fix",
    });

    await insertRow("ds-1", { values: { 金额: 1 }, reason: "new line" });
    expect(post).toHaveBeenCalledWith("/kb/datasets/ds-1/rows", {
      values: { 金额: 1 },
      reason: "new line",
    });

    await deleteRow("ds-1", "a/b", { reason: "wrong row" });
    expect(del).toHaveBeenLastCalledWith("/kb/datasets/ds-1/rows/a%2Fb", {
      params: { reason: "wrong row" },
    });

    // The version the maintainer was looking at travels as a query parameter, so a
    // stale edit is refused rather than silently applied over somebody else's.
    await deleteRow("ds-1", "a/b", { reason: "wrong row", expected_version: 7 });
    expect(del).toHaveBeenLastCalledWith("/kb/datasets/ds-1/rows/a%2Fb", {
      params: { reason: "wrong row", expected_version: "7" },
    });

    await restoreRow("ds-1", "a/b", { reason: "it was right" });
    expect(post).toHaveBeenLastCalledWith("/kb/datasets/ds-1/rows/a%2Fb/restore", {
      reason: "it was right",
    });
  });
});

describe("versions", () => {
  it("reads a bounded history and compares two of it", async () => {
    get.mockResolvedValue({ items: [], total: 0 });

    await listVersions("ds-1");
    expect(get).toHaveBeenLastCalledWith("/kb/datasets/ds-1/versions", {
      params: { limit: "100" },
    });

    await compareVersions("ds-1", 2, 5);
    expect(get).toHaveBeenLastCalledWith("/kb/datasets/ds-1/compare", {
      params: { from_version: "2", to_version: "5", limit: "200" },
    });

    await compareVersions("ds-1", 2, 5, 10);
    expect(get).toHaveBeenLastCalledWith("/kb/datasets/ds-1/compare", {
      params: { from_version: "2", to_version: "5", limit: "10" },
    });
  });
});

describe("relations and the operation log", () => {
  it("lists, suggests, confirms and removes", async () => {
    get.mockResolvedValue({ items: [], total: 0 });
    post.mockResolvedValue({ relation: { id: "rel-1" }, warnings: [] });
    del.mockResolvedValue(undefined);

    await listRelations("ds-1");
    expect(get).toHaveBeenLastCalledWith("/kb/datasets/ds-1/relations");

    await relationCandidates("ds-1");
    expect(get).toHaveBeenLastCalledWith("/kb/datasets/ds-1/relation-candidates");

    await createRelation("ds-1", {
      target_dataset_id: "ds-2",
      name: "orders → customers",
      cardinality: "many_to_one",
      local_fields: ["客户编号"],
      foreign_fields: ["编号"],
    });
    expect(post).toHaveBeenCalledWith("/kb/datasets/ds-1/relations", {
      target_dataset_id: "ds-2",
      name: "orders → customers",
      cardinality: "many_to_one",
      local_fields: ["客户编号"],
      foreign_fields: ["编号"],
    });

    await deleteRelation("ds-1", "rel-1");
    expect(del).toHaveBeenLastCalledWith("/kb/datasets/ds-1/relations/rel-1");

    await listOperations("ds-1");
    expect(get).toHaveBeenLastCalledWith("/kb/datasets/ds-1/operations", {
      params: { limit: "100" },
    });
  });
});

describe("imports", () => {
  it("uploads the workbook as a file, not as a body", async () => {
    const file = new File(["x"], "orders.xlsx");
    upload.mockResolvedValueOnce({ id: "imp-1", sheets: [] });

    await uploadWorkbook("kb-1", file, { onProgress: () => undefined });

    expect(upload).toHaveBeenCalledWith("/kb/kb-1/datasets/imports", file, {
      onProgress: expect.any(Function),
    });
  });

  it("publishes the sheets a person chose, with their note", async () => {
    post.mockResolvedValueOnce({ import_id: "imp-1", status: "queued", progress: {} });

    await commitImport("kb-1", "imp-1", {
      sheets: [{ sheet_name: "订单明细", dataset_id: "ds-1" }],
      note: "monthly refresh",
    });

    expect(post).toHaveBeenCalledWith("/kb/kb-1/datasets/imports/imp-1/commit", {
      sheets: [{ sheet_name: "订单明细", dataset_id: "ds-1" }],
      note: "monthly refresh",
    });
  });

  it("publishes the joins the person accepted alongside the sheets", async () => {
    post.mockResolvedValueOnce({ import_id: "imp-1", status: "queued", progress: {} });

    await commitImport("kb-1", "imp-1", {
      // Two new datasets, so the sheets are named rather than targeted: the
      // join is by sheet name and the ids do not exist yet.
      sheets: [
        { sheet_name: "订单明细", dataset_name: "订单明细" },
        { sheet_name: "客户", dataset_name: "客户" },
      ],
      relations: [
        {
          from_sheet: "订单明细",
          from_fields: ["客户编号"],
          to_sheet: "客户",
          to_fields: ["编号"],
          cardinality: "many_to_one",
        },
      ],
    });

    expect(post).toHaveBeenLastCalledWith("/kb/kb-1/datasets/imports/imp-1/commit", {
      sheets: [
        { sheet_name: "订单明细", dataset_name: "订单明细" },
        { sheet_name: "客户", dataset_name: "客户" },
      ],
      relations: [
        {
          from_sheet: "订单明细",
          from_fields: ["客户编号"],
          to_sheet: "客户",
          to_fields: ["编号"],
          cardinality: "many_to_one",
        },
      ],
    });
  });

  it("reads a publish's progress until the worker is done with it", async () => {
    const relation = {
      from_sheet: "订单",
      to_sheet: "客户",
      created: true,
      name: "to_客户",
      relation_id: "rel-1",
    };
    get
      .mockResolvedValueOnce({
        status: "running",
        progress: {
          phase: "running",
          total: 2,
          done: 1,
          failed: 0,
          current_sheet: "客户",
          percent: 50,
        },
        sheets: [{ sheet_name: "订单", status: "committed" }],
      })
      .mockResolvedValueOnce({
        status: "partial",
        progress: {
          phase: "done",
          total: 2,
          done: 2,
          failed: 1,
          current_sheet: null,
          percent: 100,
        },
        sheets: [{ sheet_name: "订单", status: "committed" }],
        relations: [relation],
      });
    const seen: number[] = [];

    const result = await waitForImport("kb-1", "imp-1", {
      onProgress: (progress) => seen.push(progress.percent),
      sleep: async () => undefined,
    });

    expect(get).toHaveBeenCalledWith("/kb/kb-1/datasets/imports/imp-1");
    expect(seen).toEqual([50, 100]);
    // A published sheet and a failed one: `partial`, with the count that says why.
    expect(result).toEqual({
      status: "partial",
      sheets: [{ sheet_name: "订单", status: "committed" }],
      failures: 1,
      // The joins the worker made after publishing carry through the wait, so a
      // caller that polled (rather than subscribing) can still report them.
      relations: [relation],
    });
  });

  it("reports no relations when the record has none", async () => {
    get.mockResolvedValueOnce({
      status: "committed",
      progress: { phase: "done", total: 1, done: 1, failed: 0, current_sheet: null, percent: 100 },
      sheets: [{ sheet_name: "订单", status: "committed" }],
    });

    const result = await waitForImport("kb-1", "imp-1", { sleep: async () => undefined });

    expect(result.relations).toEqual([]);
  });

  it("asks the model for advice without forcing a re-read by default", async () => {
    post.mockResolvedValueOnce({ ran: false, reason: "no_model", sheets: [] });

    await analyzeImport("kb-1", "imp-1");

    expect(post).toHaveBeenCalledWith("/kb/kb-1/datasets/imports/imp-1/analyze", {});
  });

  it("forces a re-read when the person asks for one", async () => {
    post.mockResolvedValueOnce({ ran: true, reason: null, sheets: [] });

    await analyzeImport("kb-1", "imp-1", { force: true });

    // The cached answer is what `force` bypasses, so it has to reach the URL.
    expect(post).toHaveBeenCalledWith("/kb/kb-1/datasets/imports/imp-1/analyze?force=true", {});
  });

  it("stops waiting when the poll is cancelled", async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(
      waitForImport("kb-1", "imp-1", {
        signal: controller.signal,
        sleep: async () => undefined,
      }),
    ).rejects.toThrow();

    expect(get).not.toHaveBeenCalled();
  });

  it("waits a real interval between polls when no sleep is injected", async () => {
    get
      .mockResolvedValueOnce({
        status: "running",
        progress: {
          phase: "running",
          total: 1,
          done: 0,
          failed: 0,
          current_sheet: null,
          percent: 0,
        },
        sheets: [],
      })
      .mockResolvedValueOnce({
        status: "committed",
        progress: {
          phase: "done",
          total: 1,
          done: 1,
          failed: 0,
          current_sheet: null,
          percent: 100,
        },
        sheets: [{ sheet_name: "订单", status: "committed" }],
      });

    // Zero interval but the module's own `sleep`, which is the line under test:
    // a caller that passes nothing still gets a timer rather than a tight loop.
    const result = await waitForImport("kb-1", "imp-1", { intervalMs: 0 });

    expect(result.status).toBe("committed");
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("gives up instead of waiting forever when the worker never settles", async () => {
    get.mockResolvedValue({
      status: "running",
      progress: { phase: "running", total: 1, done: 0, failed: 0, current_sheet: null, percent: 0 },
      sheets: [],
    });

    // A deadline already in the past. The ceiling exists for a job whose worker
    // died; it has to surface as an error, not a spinner that never ends.
    await expect(waitForImport("kb-1", "imp-1", { timeoutMs: -1, intervalMs: 0 })).rejects.toThrow(
      /did not finish in time/,
    );
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("keeps the wizard's decisions without publishing anything", async () => {
    put.mockResolvedValueOnce({ step: "validate", sheets: [] });

    await saveImportDraft("kb-1", "imp-1", { step: "validate", sheets: [] });

    expect(put).toHaveBeenCalledWith("/kb/kb-1/datasets/imports/imp-1", {
      step: "validate",
      sheets: [],
    });
  });
});
