import type { APIRequestContext } from "@playwright/test";
import * as XLSX from "xlsx";

import { expect, test } from "./fixtures";

import { AUTH_STATE, pageHeading } from "./helpers";

test.use({ storageState: AUTH_STATE });

/** The base this spec makes, and the workbook it publishes into it. */
const BASE_NAME = "E2E Sales Base";
const SHEET = "订单";

/**
 * A two-row workbook, made here rather than kept as a binary fixture.
 *
 * The point of the import journey is that a real spreadsheet reaches the table
 * as rows; a checked-in file would go stale silently, and its sheet names would
 * have to be discovered by opening it.
 */
function workbook(): Buffer {
  const sheet = XLSX.utils.aoa_to_sheet([
    ["订单编号", "金额"],
    ["SO-1", 100],
    ["SO-2", 250],
  ]);
  const book = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(book, sheet, SHEET);
  return XLSX.write(book, { type: "buffer", bookType: "xlsx" }) as Buffer;
}

interface BaseRow {
  id: string;
  name: string;
}

/**
 * Remove anything a previous run left behind.
 *
 * On entry rather than on exit, as everywhere else in this suite: a run that
 * fails halfway has to be followed by a run that still works.
 */
async function clearPrevious(request: APIRequestContext): Promise<void> {
  const response = await request.get("/api/kb");
  expect(response.ok(), `/api/kb answered ${status(response)}`).toBe(true);
  const { items } = (await response.json()) as { items: BaseRow[] };
  for (const base of items.filter((entry) => entry.name === BASE_NAME)) {
    await request.delete(`/api/kb/${base.id}`);
  }
}

function status(response: { status(): number }): number {
  return response.status();
}

/**
 * Importing a workbook, and the rows that come out of it (#T19 / #T07).
 *
 * The wizard is six steps deep and every one of them can leave the dialog in a
 * state the table never sees, so the assertion that matters is not "the dialog
 * closed" but "the rows are in the table, one version later".
 */
test("publishes a workbook through the wizard and shows its rows", async ({ page, request }) => {
  await clearPrevious(request);

  const created = await request.post("/api/kb/datasets/bases", {
    data: { name: BASE_NAME, description: "Made by the dataset e2e spec" },
  });
  expect(created.ok(), `creating the base answered ${created.status()}`).toBe(true);

  await page.goto("/datasets");
  await expect(pageHeading(page, "Datasets")).toBeVisible();
  await page.getByRole("link", { name: new RegExp(BASE_NAME) }).click();

  await expect(pageHeading(page, BASE_NAME)).toBeVisible();
  await page.getByRole("button", { name: "Import Excel" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Import a workbook")).toBeVisible();
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "orders.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: workbook(),
  });

  // The parser reads the book before anything is published: the sheet is found
  // and ticked, because it parses as data rather than as a cover page.
  await expect(dialog.getByText("1 sheet(s) found")).toBeVisible({ timeout: 30_000 });
  await dialog.getByRole("button", { name: "Next" }).click();

  // The model the parser proposed, which is what the columns step shows.
  await expect(dialog.getByLabel("Dataset name")).toBeVisible();
  await dialog.getByRole("button", { name: "Next" }).click();

  await expect(dialog.getByText("What will be published")).toBeVisible();
  await dialog.getByRole("button", { name: /^Publish 1 sheet/ }).click();

  // Publishing is a background job: the dialog reports the phase and then the
  // result, and only the result closes the wizard.
  await expect(dialog.getByText(/Published 1 sheet/)).toBeVisible({ timeout: 60_000 });
  await dialog.getByRole("button", { name: "Done" }).click();

  await expect(page.getByText("SO-1")).toBeVisible();
  await expect(page.getByText("SO-2")).toBeVisible();
});

/**
 * A historical answer, and the data it was computed from, four versions later
 * (#T22 / #T23).
 *
 * The conversation is stubbed rather than driven through the model: what is
 * under test here is the console's own behaviour - that the frozen answer keeps
 * its numbers, that a notice says they have moved on, and that "view the latest"
 * changes the citation without rewriting the message.
 */
test("warns that a historical answer's data has moved on, without rewriting it", async ({
  page,
}) => {
  const version = 3;
  const latest = 5;

  await page.route("**/api/kb/datasets/ds-1/versions**", (route) =>
    route.fulfill({
      json: {
        items: [1, 2, 3, 4, 5].map((no) => ({
          id: `v-${no}`,
          dataset_id: "ds-1",
          version_no: no,
          kind: "import",
          published_at: "2026-10-15T09:00:00Z",
          rows_added: 0,
          rows_changed: 0,
          rows_removed: 0,
          rows_unchanged: 0,
          current: no === latest,
        })),
        total: 5,
      },
    }),
  );
  await page.route("**/api/kb/datasets/ds-1/operations**", (route) =>
    route.fulfill({ json: { items: [], total: 0 } }),
  );
  await page.route("**/api/kb/kb-1/datasets**", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "ds-1",
            name: "销售明细",
            current_version_no: latest,
            row_count: 42,
            fields: [],
          },
        ],
        total: 1,
      },
    }),
  );

  const query = JSON.stringify({
    dataset_id: "ds-1",
    kb_id: "kb-1",
    dataset: "销售明细",
    version_vector: { 销售明细: version },
    columns: ["region", "revenue"],
    rows: [{ region: "华东", revenue: 120 }],
    row_count: 1,
  });
  const message = {
    id: "m-1",
    role: "assistant",
    content: "华东的营收是 120。",
    created_at: "2026-10-15T10:00:00Z",
    parts: [
      {
        id: "p-1",
        type: "tool",
        tool_call: { id: "tc-1", name: "query_dataset", arguments: {}, result: query },
      },
    ],
  };

  await page.route("**/api/conversations/**/messages**", (route) =>
    route.fulfill({ json: { items: [message], total: 1 } }),
  );
  await page.route("**/api/conversations/**", (route) =>
    route.fulfill({
      json: { id: "c-1", title: "营收", created_at: "2026-10-15T10:00:00Z" },
    }),
  );

  await page.goto("/chat/c-1");

  // Frozen, and says so: the answer keeps v3's numbers and the notice names both
  // versions rather than replacing anything.
  await expect(page.getByText("华东的营收是 120。")).toBeVisible({ timeout: 30_000 });
  const notice = page.getByTestId("dataset-notice-moved");
  await expect(notice).toContainText(`moved on to v${latest}`);
  await expect(notice).toContainText(`read v${version}`);

  await notice.getByRole("button", { name: "View the latest" }).click();
  await expect(notice.getByRole("button", { name: `Back to v${version}` })).toBeVisible();
  await expect(page.getByText("华东的营收是 120。")).toBeVisible();
});
