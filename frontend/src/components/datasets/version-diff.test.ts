import { describe, expect, it } from "vitest";

import { describeChange, formatCell } from "./version-diff";
import type { RowChange } from "@/types/datasets";

/**
 * The cell-level diff behind the comparison view.
 *
 * "金额 changed" is the question a reviewer opens the row to answer; "100 → 150"
 * is the answer. These assertions are about which cells are highlighted and
 * which are left alone, because a diff that highlights everything is a diff
 * nobody reads.
 */

const row = (next: Partial<RowChange>): RowChange => ({
  record_key: "so-1",
  change: "changed",
  before: null,
  after: null,
  changed_fields: [],
  ...next,
});

describe("formatCell", () => {
  it("shows an absent value as a dash rather than the word null", () => {
    expect(formatCell(null)).toBe("—");
    expect(formatCell(undefined)).toBe("—");
    expect(formatCell("")).toBe("—");
  });

  it("keeps a zero and a false visible", () => {
    // Both are values, and both used to vanish behind a falsy check.
    expect(formatCell(0)).toBe("0");
    expect(formatCell(false)).toBe("false");
  });

  it("serialises what is not a scalar", () => {
    expect(formatCell({ a: 1 })).toBe('{"a":1}');
    expect(formatCell(["x"])).toBe('["x"]');
  });
});

describe("describeChange", () => {
  it("marks the fields the backend named as changed", () => {
    const diff = describeChange(
      row({
        before: { 订单编号: "SO-1", 金额: 100, 客户编号: "C-1" },
        after: { 订单编号: "SO-1", 金额: 150, 客户编号: "C-1" },
        changed_fields: ["金额"],
      }),
    );

    expect(diff.filter((cell) => cell.changed).map((cell) => cell.field)).toEqual(["金额"]);
    expect(diff.map((cell) => cell.field)).toEqual(["订单编号", "金额", "客户编号"]);
  });

  it("keeps the document's own field order", () => {
    // A row's values arrive in model order; sorting them would be a second
    // reading of the same record.
    const diff = describeChange(
      row({
        before: { 订单编号: "SO-1", 金额: 100 },
        after: { 订单编号: "SO-1", 金额: 100 },
      }),
    );

    expect(diff.map((cell) => cell.field)).toEqual(["订单编号", "金额"]);
  });

  it("highlights a value that differs but was not listed", () => {
    // The backend's list is right; a missing highlight is a reviewer reading
    // past a change, which is the failure this guards.
    const diff = describeChange(
      row({
        before: { 金额: 100 },
        after: { 金额: 150 },
        changed_fields: [],
      }),
    );

    expect(diff).toEqual([{ field: "金额", before: 100, after: 150, changed: true }]);
  });

  it("keeps a listed field highlighted even when the two spellings read the same", () => {
    // `""` and `null` both render as a dash, and they are different stored
    // values: the row is in the list because the backend compared what is
    // stored. Un-highlighting it would leave a row marked "changed" whose diff
    // shows nothing, which reads as a bug in the comparison.
    const diff = describeChange(
      row({ before: { 备注: "" }, after: { 备注: null }, changed_fields: ["备注"] }),
    );

    expect(diff[0]).toMatchObject({ field: "备注", changed: true });
    expect(formatCell(diff[0]?.before)).toBe("—");
    expect(formatCell(diff[0]?.after)).toBe("—");
  });

  it("shows an added row as its cells, not as an empty panel", () => {
    const diff = describeChange(
      row({ change: "added", before: null, after: { 订单编号: "SO-9", 金额: 300 } }),
    );

    expect(diff.map((cell) => cell.field)).toEqual(["订单编号", "金额"]);
    expect(diff.every((cell) => cell.changed)).toBe(true);
    expect(diff[0]?.before).toBeUndefined();
  });

  it("shows a removed row as what it used to say", () => {
    const diff = describeChange(
      row({ change: "removed", before: { 订单编号: "SO-9", 金额: 300 }, after: null }),
    );

    expect(diff.map((cell) => cell.after)).toEqual([undefined, undefined]);
    expect(diff[0]?.before).toBe("SO-9");
  });

  it("answers an absent row with nothing to compare", () => {
    // A row the file did not mention has no values on either side; the change
    // is the absence itself, and its own badge says so.
    expect(describeChange(row({ change: "missing", before: null, after: null }))).toEqual([]);
  });

  it("includes a column only the new version has", () => {
    const diff = describeChange(
      row({ before: { 订单编号: "SO-1" }, after: { 订单编号: "SO-1", 备注: "加急" } }),
    );

    expect(diff.map((cell) => cell.field)).toEqual(["订单编号", "备注"]);
    expect(diff[1]).toMatchObject({ field: "备注", before: undefined, after: "加急", changed: true });
  });
});
