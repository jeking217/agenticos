/**
 * The import wizard's decisions, as functions rather than state.
 *
 * A six-step dialog is mostly rules: which sheets are in, what a column is
 * called, which one identifies a row, whether the step can be left, and what
 * the commit body looks like. Held inside `useState` those rules can only be
 * asserted by rendering the dialog and clicking through it; here they are the
 * thing under test, and the component is the thing that draws them.
 *
 * The one rule worth naming: **one key per dataset**. A composite key is
 * something the model can hold, and it is not something to discover by ticking
 * two checkboxes - so choosing a key clears the others.
 */

import type { CommitSheet, DatasetFieldType, SheetProposal } from "@/types/datasets";

/** The wizard's six steps, in the order a person meets them. */
export const WIZARD_STEPS = [
  "upload",
  "sheets",
  "columns",
  "validate",
  "publish",
  "report",
] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

/** The types the model offers for a column. */
export const FIELD_TYPES: DatasetFieldType[] = [
  "text",
  "integer",
  "numeric",
  "date",
  "timestamp",
  "boolean",
];

/** One column of one sheet: kept or dropped, renamed, retyped, or the key. */
export interface ColumnChoice {
  keep: boolean;
  name: string;
  type: DatasetFieldType;
  isKey: boolean;
}

export interface SheetChoice {
  checked: boolean;
  name: string;
  /** Empty means "create a new dataset"; otherwise an existing dataset's id. */
  target: string;
  columns: ColumnChoice[];
}

export type SheetChoices = Record<string, SheetChoice>;

/**
 * What the parser proposed, as the wizard's starting point.
 *
 * `checked` follows `kind` rather than defaulting everything to on: a workbook's
 * cover page and its totals block parse as sheets, and importing them as data is
 * the mistake this saves.
 */
export function initialChoices(sheets: SheetProposal[]): SheetChoices {
  return Object.fromEntries(
    sheets.map((sheet) => [
      sheet.sheet_name,
      {
        checked: sheet.kind === "data",
        name: sheet.sheet_name,
        target: "",
        columns: sheet.fields.map((field) => ({
          keep: true,
          name: field.name,
          type: field.data_type,
          isKey: field.is_primary_key,
        })),
      },
    ]),
  );
}

/** The sheets that will be published, in workbook order. */
export function checkedSheets(sheets: SheetProposal[], choices: SheetChoices): SheetProposal[] {
  return sheets.filter((sheet) => choices[sheet.sheet_name]?.checked === true);
}

export function keptColumns(choice: SheetChoice): ColumnChoice[] {
  return choice.columns.filter((column) => column.keep);
}

/**
 * Whether this sheet can be published as it stands.
 *
 * An existing dataset needs nothing: its model already exists and the columns
 * are matched to it by name. A new one needs a name, at least one column, a
 * key, and a name for every column it keeps - a field called "" is a column
 * nobody can query.
 */
export function isReadyToPublish(choice: SheetChoice): boolean {
  if (choice.target) return true;
  const kept = keptColumns(choice);
  return (
    choice.name.trim().length > 0 &&
    kept.length > 0 &&
    kept.some((column) => column.isKey) &&
    kept.every((column) => column.name.trim().length > 0)
  );
}

export function allReadyToPublish(sheets: SheetProposal[], choices: SheetChoices): boolean {
  const selected = checkedSheets(sheets, choices);
  return (
    selected.length > 0 &&
    selected.every((sheet) => {
      const choice = choices[sheet.sheet_name];
      return choice !== undefined && isReadyToPublish(choice);
    })
  );
}

/** Apply a sheet-level change. An unknown sheet is left alone, not invented. */
export function mergeSheet(
  choices: SheetChoices,
  sheetName: string,
  next: Partial<Omit<SheetChoice, "columns">>,
): SheetChoices {
  const choice = choices[sheetName];
  if (!choice) return choices;
  return { ...choices, [sheetName]: { ...choice, ...next } };
}

/**
 * Apply a column-level change, honouring the single-key rule.
 *
 * Choosing a key keeps the column (a key that is not imported is not a key) and
 * clears every other, in the same pass: two keys would make the record identity
 * depend on which one the backend read first.
 */
export function mergeColumn(
  choices: SheetChoices,
  sheetName: string,
  index: number,
  next: Partial<ColumnChoice>,
): SheetChoices {
  const choice = choices[sheetName];
  if (!choice) return choices;
  const columns = choice.columns.map((column, at) => {
    if (at !== index) return next.isKey ? { ...column, isKey: false } : column;
    return next.isKey ? { ...column, ...next, keep: true } : { ...column, ...next };
  });
  return { ...choices, [sheetName]: { ...choice, columns } };
}

/** The commit body: one entry per checked sheet, existing or new. */
export function commitPayload(sheets: SheetProposal[], choices: SheetChoices): CommitSheet[] {
  return checkedSheets(sheets, choices).flatMap((sheet): CommitSheet[] => {
    const choice = choices[sheet.sheet_name];
    if (!choice) return [];
    if (choice.target) return [{ sheet_name: sheet.sheet_name, dataset_id: choice.target }];
    const kept = keptColumns(choice);
    return [
      {
        sheet_name: sheet.sheet_name,
        dataset_name: choice.name.trim(),
        primary_key_fields: kept.filter((column) => column.isKey).map((column) => column.name),
        fields: kept.map((column) => ({
          name: column.name.trim(),
          data_type: column.type,
          is_primary_key: column.isKey,
        })),
      },
    ];
  });
}

/**
 * The draft step to save at, or `null` for a step there is nothing to resume.
 *
 * The backend's steps are its own (`sheets`, `model`, `validate`); `upload` has
 * no decisions yet and `publish`/`report` are past the point of resuming.
 */
export function draftStepFor(step: WizardStep): "sheets" | "model" | "validate" | null {
  switch (step) {
    case "sheets":
      return "sheets";
    case "columns":
      return "model";
    case "validate":
      return "validate";
    default:
      return null;
  }
}
