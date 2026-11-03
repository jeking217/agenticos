import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { SheetProposal } from "@/types/datasets";

import { ImportWorkbookDialog } from "./import-workbook-dialog";

/**
 * What the upload step and the confirmation step say (#T19).
 *
 * One string carries the percentage and the bar beside it reads the same one, so
 * both have to be given it: a translator handed a message with `{percent}` and
 * no value for it reports a formatting error and prints the placeholder, which
 * is worse than printing nothing at all.
 *
 * The confirmation step has the opposite problem - it draws the sheet, and a
 * thirteen-column quote table is wider than the dialog. Folding it to fit turned
 * every cell into a column of single characters, so the table has to keep its
 * natural width and let the box scroll.
 */

const upload = vi.hoisted(() => ({
  isPending: true,
  mutateAsync: vi.fn((vars: { onProgress: (fraction: number) => void }) => {
    vars.onProgress(0.42);
    // Never settles: the first assertion is about the bar while the request is
    // out. The later tests replace this with a resolved workbook.
    return new Promise(() => undefined);
  }),
}));

const analyze = vi.hoisted(() => ({
  isPending: false,
  // "No model configured" is an answer, not a failure: it keeps the wizard on
  // the parser's proposal rather than in its error state.
  mutateAsync: vi.fn(async () => ({
    ran: false,
    reason: "no_model",
    provider: null,
    model: null,
    sheets: [],
    relations: [],
  })),
}));

vi.mock("@/hooks/use-datasets", () => ({
  useUploadWorkbook: () => upload,
  useCommitImport: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useAnalyzeImport: () => analyze,
  useDatasets: () => ({ datasets: [] }),
}));

function renderDialog() {
  // Radix mounts the content in a portal, so the file input is not inside the
  // container the render call returns.
  render(<ImportWorkbookDialog kbId="kb-1" open onOpenChange={vi.fn()} datasets={[]} />);
}

function chooseFile() {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  expect(input).not.toBeNull();
  fireEvent.change(input as HTMLInputElement, {
    target: { files: [new File(["rows"], "sales.xlsx")] },
  });
}

const quotes: SheetProposal = {
  sheet_name: "重量段报价",
  kind: "data",
  header_row: 1,
  row_count: 153,
  column_count: 3,
  fields: [
    {
      name: "报价ID",
      display_name: "报价ID",
      data_type: "text",
      is_nullable: false,
      is_primary_key: true,
      distinct_ratio: 1,
      samples: ["Q0001"],
    },
    {
      name: "渠道名称",
      display_name: "渠道名称",
      data_type: "text",
      is_nullable: true,
      is_primary_key: false,
      distinct_ratio: 0.2,
      samples: ["美国空派小包货包税大陆飞"],
    },
    {
      name: "单价",
      display_name: "单价",
      data_type: "numeric",
      is_nullable: true,
      is_primary_key: false,
      distinct_ratio: 0.6,
      samples: ["181"],
    },
  ],
  preview: [{ 报价ID: "Q0001", 渠道名称: "美国空派小包货包税大陆飞", 单价: 181 }],
  warnings: [],
  merged_cells: 0,
};

describe("the workbook upload", () => {
  it("reads out the percentage it has reached", async () => {
    renderDialog();
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();

    fireEvent.change(input as HTMLInputElement, {
      target: { files: [new File(["rows"], "sales.xlsx")] },
    });

    // The visible line, and the bar's own name: a screen reader gets the same
    // fact the sighted reader does.
    expect(await screen.findByText("Uploading… 42%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAccessibleName("Uploading… 42%");
  });
});

describe("the confirmation step", () => {
  it("lets a wide sheet scroll sideways rather than folding its columns", async () => {
    upload.mutateAsync.mockResolvedValueOnce({ id: "imp-1", sheets: [quotes] });
    renderDialog();
    chooseFile();

    // Upload → sheets → columns → validate.
    fireEvent.click(await screen.findByRole("button", { name: /next/i }));
    fireEvent.click(await screen.findByRole("button", { name: /next/i }));

    const preview = await screen.findByTestId("sheet-preview");
    const table = preview.querySelector("table");

    // `w-max` is the natural width that gives the box something to scroll;
    // `w-full` (before) pinned the table to the dialog and wrapped every cell.
    expect(preview.className).toContain("overflow-x-auto");
    expect(table?.className).toContain("w-max");
    expect(table?.className).toContain("min-w-full");
    expect(preview.querySelector("td")?.className).toContain("whitespace-nowrap");
    expect(preview.querySelector("th")?.className).toContain("whitespace-nowrap");
  });

  it("shows the sheet's own values under the names the columns have now", async () => {
    upload.mutateAsync.mockResolvedValueOnce({ id: "imp-1", sheets: [quotes] });
    renderDialog();
    chooseFile();

    fireEvent.click(await screen.findByRole("button", { name: /next/i }));
    const rename = await screen.findByDisplayValue("单价");
    fireEvent.change(rename, { target: { value: "价格" } });
    fireEvent.click(await screen.findByRole("button", { name: /next/i }));

    const preview = await screen.findByTestId("sheet-preview");
    expect(preview.querySelector("th:last-child")?.textContent).toBe("价格");
    // The value is still read by the name the parser gave the column, so
    // renaming it does not empty its preview.
    expect(preview.querySelector("td:last-child")?.textContent).toBe("181");
  });
});
