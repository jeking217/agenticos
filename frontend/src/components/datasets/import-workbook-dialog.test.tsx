import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ImportWorkbookDialog } from "./import-workbook-dialog";

/**
 * The upload step says how far it has got (#T19).
 *
 * One string carries the number and the bar beside it reads the same one, so
 * both have to be given it: a translator handed a message with `{percent}` and
 * no value for it reports a formatting error and prints the placeholder, which
 * is worse than printing nothing at all.
 */

const upload = vi.hoisted(() => ({
  isPending: true,
  mutateAsync: vi.fn((vars: { onProgress: (fraction: number) => void }) => {
    vars.onProgress(0.42);
    // Never settles: the assertions are about the bar while the request is out.
    return new Promise(() => undefined);
  }),
}));

vi.mock("@/hooks/use-datasets", () => ({
  useUploadWorkbook: () => upload,
  useCommitImport: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useDatasets: () => ({ datasets: [] }),
}));

function renderDialog() {
  // Radix mounts the content in a portal, so the file input is not inside the
  // container the render call returns.
  render(<ImportWorkbookDialog kbId="kb-1" open onOpenChange={vi.fn()} datasets={[]} />);
}

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
