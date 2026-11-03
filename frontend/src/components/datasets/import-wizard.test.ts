import { describe, expect, it } from "vitest";

import {
  allReadyToPublish,
  checkedSheets,
  commitPayload,
  draftStepFor,
  initialChoices,
  isReadyToPublish,
  keptColumns,
  mergeColumn,
  mergeSheet,
  WIZARD_STEPS,
} from "./import-wizard";
import type { SheetChoices, SheetChoice } from "./import-wizard";
import type { SheetProposal } from "@/types/datasets";

/**
 * The wizard's rules, asserted without rendering it.
 *
 * The dialog is a view over these functions, so what is worth pinning is the set
 * of decisions that produce a valid publish: what the parser's proposal starts
 * as, what a step can be left with, and what the commit body ends up saying.
 */

const ORDERS: SheetProposal = {
  sheet_name: "订单",
  kind: "data",
  header_row: 1,
  row_count: 2,
  column_count: 2,
  fields: [
    {
      name: "订单编号",
      display_name: "订单编号",
      data_type: "text",
      is_nullable: false,
      is_primary_key: true,
      distinct_ratio: 1,
      samples: ["SO-1"],
    },
    {
      name: "金额",
      display_name: "金额",
      data_type: "integer",
      is_nullable: true,
      is_primary_key: false,
      distinct_ratio: 0.5,
      samples: ["100"],
    },
  ],
  preview: [],
  warnings: [],
  merged_cells: 0,
};

const COVER: SheetProposal = { ...ORDERS, sheet_name: "封面", kind: "summary", fields: [] };

/**
 * One sheet's choices, which the workbook built above is known to have.
 *
 * Indexed access is checked here, so the assertions below read as the plain
 * lookups they are.
 */
function at(choices: SheetChoices, sheet: string): SheetChoice {
  const found = choices[sheet];
  if (!found) throw new Error(`no choices for ${sheet}`);
  return found;
}

describe("what a workbook starts as", () => {
  it("checks the data sheets and leaves the rest alone", () => {
    // A cover page parses as a sheet; importing it as data is the mistake this
    // default exists to prevent.
    const choices = initialChoices([ORDERS, COVER]);

    expect(at(choices, "订单").checked).toBe(true);
    expect(at(choices, "封面").checked).toBe(false);
  });

  it("keeps the parser's key and types as the starting suggestion", () => {
    const choices = initialChoices([ORDERS]);

    expect(at(choices, "订单").columns).toEqual([
      { keep: true, name: "订单编号", type: "text", isKey: true },
      { keep: true, name: "金额", type: "integer", isKey: false },
    ]);
  });
});

describe("one key per dataset", () => {
  it("clears the previous key when another column is chosen", () => {
    const choices = mergeColumn(initialChoices([ORDERS]), "订单", 1, { isKey: true });

    expect(at(choices, "订单").columns.map((column) => column.isKey)).toEqual([false, true]);
  });

  it("keeps a column that is made the key", () => {
    // A key that is not imported is not a key: the record identity would be
    // computed from a column the rows do not carry.
    const dropped = mergeColumn(initialChoices([ORDERS]), "订单", 1, { keep: false });

    const keyed = mergeColumn(dropped, "订单", 1, { isKey: true });

    expect(at(keyed, "订单").columns[1]).toMatchObject({ keep: true, isKey: true });
  });

  it("ignores a change to a sheet the workbook does not have", () => {
    const choices = initialChoices([ORDERS]);

    expect(mergeColumn(choices, "不存在", 0, { keep: false })).toBe(choices);
    expect(mergeSheet(choices, "不存在", { checked: true })).toBe(choices);
  });
});

describe("whether a step can be left", () => {
  it("refuses a new dataset with no key", () => {
    const choices = mergeColumn(initialChoices([ORDERS]), "订单", 0, { isKey: false });

    expect(isReadyToPublish(at(choices, "订单"))).toBe(false);
    expect(allReadyToPublish([ORDERS], choices)).toBe(false);
  });

  it("refuses a new dataset whose name is blank", () => {
    const choices = mergeSheet(initialChoices([ORDERS]), "订单", { name: "   " });

    expect(isReadyToPublish(at(choices, "订单"))).toBe(false);
  });

  it("refuses a column with no name", () => {
    const choices = mergeColumn(initialChoices([ORDERS]), "订单", 1, { name: "" });

    expect(isReadyToPublish(at(choices, "订单"))).toBe(false);
  });

  it("accepts a sheet that extends an existing dataset as it stands", () => {
    // The target's model already exists; its columns are matched by name, so
    // there is nothing here to decide.
    const choices = mergeSheet(initialChoices([ORDERS]), "订单", { target: "ds-1" });

    expect(isReadyToPublish(at(choices, "订单"))).toBe(true);
  });

  it("refuses a workbook with nothing checked", () => {
    const choices = mergeSheet(initialChoices([ORDERS]), "订单", { checked: false });

    expect(checkedSheets([ORDERS], choices)).toEqual([]);
    expect(allReadyToPublish([ORDERS], choices)).toBe(false);
  });
});

describe("the commit body", () => {
  it("describes a new dataset with the columns as they were edited", () => {
    let choices = initialChoices([ORDERS]);
    choices = mergeSheet(choices, "订单", { name: " 订单明细 " });
    choices = mergeColumn(choices, "订单", 1, { name: "金额", type: "numeric" });

    expect(commitPayload([ORDERS], choices)).toEqual([
      {
        sheet_name: "订单",
        dataset_name: "订单明细",
        primary_key_fields: ["订单编号"],
        fields: [
          { name: "订单编号", data_type: "text", is_primary_key: true },
          { name: "金额", data_type: "numeric", is_primary_key: false },
        ],
      },
    ]);
  });

  it("leaves a dropped column out of both the fields and the key", () => {
    const choices = mergeColumn(initialChoices([ORDERS]), "订单", 0, { keep: false });

    const payload = commitPayload([ORDERS], choices);

    expect(payload).toHaveLength(1);
    expect(payload[0]?.fields?.map((field) => field.name)).toEqual(["金额"]);
    expect(payload[0]?.primary_key_fields).toEqual([]);
    // Nothing left to key on, so the wizard will not let this through.
    expect(isReadyToPublish(at(choices, "订单"))).toBe(false);
    expect(keptColumns(at(choices, "订单"))).toHaveLength(1);
  });

  it("sends an existing dataset by id and nothing else", () => {
    const choices = mergeSheet(initialChoices([ORDERS]), "订单", { target: "ds-1" });

    expect(commitPayload([ORDERS], choices)).toEqual([{ sheet_name: "订单", dataset_id: "ds-1" }]);
  });

  it("publishes nothing for a sheet that was unchecked", () => {
    const choices = mergeSheet(initialChoices([ORDERS, COVER]), "封面", { checked: true });
    const unchecked = mergeSheet(choices, "封面", { checked: false });

    expect(commitPayload([ORDERS, COVER], unchecked).map((sheet) => sheet.sheet_name)).toEqual([
      "订单",
    ]);
  });
});

describe("where a draft is saved", () => {
  it("maps the wizard's steps onto the three the backend keeps", () => {
    expect(WIZARD_STEPS.map(draftStepFor)).toEqual([
      null,
      "sheets",
      "model",
      "validate",
      null,
      null,
    ]);
  });
});
