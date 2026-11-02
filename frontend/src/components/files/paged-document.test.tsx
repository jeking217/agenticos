import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { PagedDocument } from "./paged-document";

/** Three pages of markup, each one carrying the word for its position. */
const PAGES = ["first", "second", "third"].map((word) => `<p>${word}</p>`);

function draw(props: Partial<React.ComponentProps<typeof PagedDocument>> = {}) {
  return render(<PagedDocument pages={PAGES} width={960} height={540} {...props} />);
}

/** What is on the stage, as opposed to in the rail. */
function stageText(): string {
  return screen.getByTestId("page-stage").textContent ?? "";
}

/**
 * The two-pane document: a rail of pages, one page on the stage.
 *
 * The rail is the whole point of the shape - a deck read as one long column is a
 * scroll through a document to look at one page of it - so what these pin is that
 * the rail lists every page, that picking one moves the stage, and that the counter
 * and the arrows agree with each other.
 */
describe("PagedDocument", () => {
  it("lists every page in the rail, numbered", () => {
    draw();
    expect(screen.getAllByRole("button", { name: /^Page \d+$/ })).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Page 3" })).toBeInTheDocument();
  });

  it("opens on the page a caller picked elsewhere", () => {
    draw({ initialPage: 2 });
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
    expect(stageText()).toContain("third");
  });

  it("moves the stage when a thumbnail is picked, and marks it", async () => {
    const user = userEvent.setup();
    draw();
    expect(screen.getByRole("button", { name: "Page 1" })).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Page 2" }));
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Page 2" })).toHaveAttribute("aria-current", "page");
    expect(stageText()).toContain("second");
  });

  it("pages with the arrows, and stops at the ends", async () => {
    const user = userEvent.setup();
    draw();
    const previous = screen.getByRole("button", { name: "Previous page" });
    const next = screen.getByRole("button", { name: "Next page" });
    expect(previous).toBeDisabled();

    await user.click(next);
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    await user.click(next);
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
    expect(next).toBeDisabled();
  });

  it("zooms in steps, and gives the whole page back", async () => {
    const user = userEvent.setup();
    draw();
    // No layout in a test environment, so "fitted" is exactly the page's own size.
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByRole("button", { name: "110%" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "110%" }));
    expect(screen.getByRole("button", { name: "100%" })).toBeInTheDocument();
  });

  it("draws no rail for a document of one page", () => {
    draw({ pages: ["<p>only</p>"] });
    expect(screen.queryByRole("button", { name: /^Page \d+$/ })).toBeNull();
    expect(screen.queryByText("1 / 1")).toBeNull();
  });
});
