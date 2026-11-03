/**
 * One changed row, as the cells that differ.
 *
 * A comparison answers with counts and a list of changed *field names*; a
 * reviewer needs the values. This turns a `RowChange` into rows of
 * `field | before | after`, which is the same shape `LineDiff` renders for text
 * - changed entries in one column each, so the eye compares two cells rather
 * than two paragraphs.
 *
 * The fields come from the two documents, not from `changed_fields` alone, and
 * in the order the documents hold: a row's values arrive in model order, and a
 * diff that re-sorted them alphabetically would be harder to read than the
 * table it explains. `changed_fields` then decides which cells are highlighted,
 * and a value that differs but was not listed is highlighted anyway - the
 * backend is right, and a missing highlight is a reviewer reading past a change.
 */

import type { RowChange } from "@/types/datasets";

export interface FieldDiff {
  field: string;
  before: unknown;
  after: unknown;
  changed: boolean;
}

/** A cell as it should be read: absent, empty and present all look different. */
export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value.length === 0 ? "—" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function sameValue(left: unknown, right: unknown): boolean {
  // Compared as they are read, not as they are stored: two spellings that
  // display the same sentence are not a difference a reviewer can see, and
  // highlighting them would teach people to ignore the highlight.
  return formatCell(left) === formatCell(right);
}

/**
 * The cell-level diff of one record.
 *
 * A row that was added has nothing before it, a row that was removed or is
 * absent has nothing after, and both are diffs worth showing: "added" is five
 * new cells, not an empty panel.
 */
export function describeChange(row: RowChange): FieldDiff[] {
  const before = row.before ?? {};
  const after = row.after ?? {};
  const listed = new Set(row.changed_fields);
  const fields = [...Object.keys(before), ...Object.keys(after).filter((key) => !(key in before))];

  return fields.map((field) => {
    const changed = listed.has(field) || !sameValue(before[field], after[field]);
    return { field, before: before[field], after: after[field], changed };
  });
}
