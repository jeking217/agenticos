import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { OperationsDrawer } from "./operations-drawer";
import type { Dataset, DatasetOperation } from "@/types/datasets";

/**
 * The operation log over the page (#T45).
 *
 * It has three doors - a row's menu, the dataset's toolbar, and a historical
 * conversation - and the two questions behind them are different: the toolbar
 * asks "what has been done to this dataset", a row asks "who touched this one,
 * and why". The drawer answers whichever was asked, and its version numbers lead
 * on to the comparison that shows what the change did.
 */

const operations: DatasetOperation[] = [
  {
    id: "op-2",
    version_no: 7,
    operation: "delete",
    record_key: "SO-1",
    before: { 金额: 100 },
    after: null,
    reason: "客户取消",
    actor_user_id: "u-1",
    created_at: "2026-11-02T09:00:00Z",
  },
  {
    id: "op-1",
    version_no: 4,
    operation: "update",
    record_key: "SO-2",
    before: { 金额: 90 },
    after: { 金额: 120 },
    reason: "补录",
    actor_user_id: "u-1",
    created_at: "2026-11-01T09:00:00Z",
  },
];

const restore = vi.hoisted(() => vi.fn());

vi.mock("@/hooks/use-datasets", () => ({
  useOperations: () => ({ operations, isLoading: false }),
  useRowMutations: () => ({ restore: { mutate: restore, isPending: false } }),
}));

const dataset = { id: "ds-7", name: "Sales", fields: [] } as unknown as Dataset;

function open(recordKey?: string) {
  const onOpenChange = vi.fn();
  const onCompare = vi.fn();
  render(
    <OperationsDrawer
      dataset={dataset}
      open
      recordKey={recordKey}
      onOpenChange={onOpenChange}
      onCompare={onCompare}
    />,
  );
  return { onOpenChange, onCompare };
}

describe("the operation log drawer", () => {
  it("shows the whole dataset's history from the toolbar", () => {
    open();

    expect(screen.getByText(/Everything done to Sales/)).toBeInTheDocument();
    expect(screen.getByText("SO-1")).toBeInTheDocument();
    expect(screen.getByText("SO-2")).toBeInTheDocument();
  });

  it("narrows to the record a row's menu asked about", () => {
    open("SO-1");

    expect(screen.getByText(/Everything done to record SO-1/)).toBeInTheDocument();
    expect(screen.queryByText("SO-2")).not.toBeInTheDocument();
  });

  it("says why a record was deleted, which is what the log is for", () => {
    open("SO-1");

    expect(screen.getByText("客户取消")).toBeInTheDocument();
  });

  it("leads on to comparing the version that made the change", async () => {
    const { onCompare, onOpenChange } = open("SO-1");

    await userEvent.click(screen.getByRole("button", { name: "Compare v7 with now" }));

    // The drawer steps out of the way: the comparison is a page, not a panel.
    expect(onCompare).toHaveBeenCalledWith(7);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("restores a record deleted by mistake, from the row it was deleted in", async () => {
    open("SO-1");

    await userEvent.click(screen.getByRole("button", { name: /Restore/ }));

    expect(restore).toHaveBeenCalledWith({ recordKey: "SO-1", reason: expect.any(String) });
  });

  it("offers no restore for a change that deleted nothing", () => {
    open("SO-2");

    expect(within(screen.getByRole("table")).queryByRole("button", { name: /Restore/ })).toBeNull();
  });
});
