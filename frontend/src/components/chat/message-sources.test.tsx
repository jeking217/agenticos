import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SourceItem } from "@/lib/chat-sources";
import type { Dataset, DatasetOperation, DatasetVersion } from "@/types/datasets";

import { MessageSources } from "./message-sources";

const push = vi.hoisted(() => vi.fn());

/** What the dataset endpoints answer with, which decides the notices (#T23). */
const server = vi.hoisted(() => ({
  datasets: [] as Dataset[],
  datasetsLoaded: true,
  versions: [] as DatasetVersion[],
  total: 0,
  operations: [] as DatasetOperation[],
}));

vi.mock("@/lib/locale-navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/hooks/use-datasets", () => ({
  useDatasets: () => ({ datasets: server.datasets, isSuccess: server.datasetsLoaded }),
  useDatasetVersions: () => ({
    versions: server.versions,
    total: server.total,
    isSuccess: true,
  }),
  useOperations: () => ({ operations: server.operations }),
}));
vi.mock("@/hooks/use-knowledge-bases", () => ({
  useKnowledgeBaseFor: () => (collection: string | undefined) =>
    collection === "support_aa" ? { id: "kb-1", name: "Support handbook" } : undefined,
}));
vi.mock("@/components/kb/source-viewer", () => ({
  SourceViewer: ({ cited, onClose }: { cited: { filename: string }; onClose: () => void }) => (
    <button type="button" onClick={onClose}>
      close {cited.filename}
    </button>
  ),
}));

const passage = (index: number, title: string, collection = "support_aa"): SourceItem => ({
  index,
  type: "rag",
  title,
  collection,
});

const page = (index: number, url: string, title = "A page"): SourceItem => ({
  index,
  type: "web",
  title,
  url,
});

const dataset = (index: number, overrides: Partial<SourceItem> = {}): SourceItem => ({
  index,
  type: "dataset",
  title: "Sales",
  datasetId: "ds-7",
  baseId: "kb-2",
  version: 5,
  ...overrides,
});

const sales = (overrides: Partial<Dataset> = {}): Dataset =>
  ({
    id: "ds-7",
    name: "Sales",
    current_version_no: 5,
    fields: [],
    ...overrides,
  }) as Dataset;

const version = (no: number, at = "2026-10-15T09:00:00Z"): DatasetVersion =>
  ({ id: `v-${no}`, version_no: no, published_at: at }) as DatasetVersion;

const deletion = (versionNo: number): DatasetOperation =>
  ({ id: `op-${versionNo}`, operation: "delete", version_no: versionNo }) as DatasetOperation;

/** What the dataset endpoints say, before each test says otherwise. */
function datasetMovedOn() {
  server.datasets = [sales()];
  server.datasetsLoaded = true;
  server.versions = [version(5), version(3)];
  server.total = 2;
  server.operations = [];
}

beforeEach(() => {
  push.mockClear();
  server.datasets = [sales()];
  server.datasetsLoaded = true;
  server.versions = [version(5)];
  server.total = 1;
  server.operations = [];
});

async function expand(sources: SourceItem[], onOpen = vi.fn()) {
  render(<MessageSources sources={sources} onOpen={onOpen} />);
  await userEvent.click(screen.getByRole("button", { expanded: false }));
  return onOpen;
}

/**
 * What an answer drew on, under it.
 *
 * Counted by document - five passages of a handbook are one source to whoever
 * reads the answer - and opening onto the documents, each of which opens the
 * panel on its passages or, for a file, the file itself.
 */
describe("the sources under an answer", () => {
  it("stacks no more than four marks, however many documents there are", () => {
    const sources = [1, 2, 3, 4, 5].map((i) => page(i, `https://site${i}.example/`));
    const { container } = render(<MessageSources sources={sources} onOpen={vi.fn()} />);

    expect(screen.getByRole("button", { name: /5 sources/ })).toBeInTheDocument();
    expect(container.querySelectorAll(".lucide-globe")).toHaveLength(4);
  });

  it("shows one of each kind of source before a second of any", () => {
    const sources = [
      passage(1, "a.md"),
      passage(2, "b.md"),
      passage(3, "c.md"),
      page(4, "https://github.com/acme/api"),
      page(5, "https://blog.example/"),
      page(6, "https://github.com/acme/web"),
    ];
    const { container } = render(<MessageSources sources={sources} onOpen={vi.fn()} />);

    // A file, GitHub and the web all make the cut ahead of a second file.
    const marks = container.querySelectorAll("button > span:first-child > span");
    expect(marks).toHaveLength(4);
    expect(container.querySelectorAll("button .lucide-globe")).toHaveLength(1);
  });

  it("names where each document is, and how many of its passages were cited", async () => {
    await expand([
      passage(1, "refunds.md"),
      passage(2, "refunds.md"),
      page(3, "https://www.notion.so/x", "Roadmap"),
    ]);

    expect(screen.getByText("Support handbook · 2 passages")).toBeInTheDocument();
    expect(screen.getByText("notion.so")).toBeInTheDocument();
  });

  it("opens the panel on a document's first passage", async () => {
    const onOpen = await expand([passage(1, "a.md"), passage(4, "b.md")]);

    await userEvent.click(screen.getByRole("button", { name: /^b\.md/ }));

    expect(onOpen).toHaveBeenCalledWith(4);
  });

  it("opens a file whole from its base, and closes it again", async () => {
    await expand([passage(1, "refunds.md")]);

    await userEvent.click(screen.getByRole("button", { name: "Open the original of refunds.md" }));
    await userEvent.click(screen.getByRole("button", { name: "close refunds.md" }));

    expect(screen.queryByRole("button", { name: "close refunds.md" })).toBeNull();
  });

  it("offers no original for a file in a base the reader cannot see", async () => {
    await expand([passage(1, "secret.md", "elsewhere")]);

    expect(screen.queryByRole("button", { name: /Open the original/ })).toBeNull();
    expect(screen.getByText("secret.md")).toBeInTheDocument();
  });

  it("links a web page out, in a tab that cannot reach back", async () => {
    await expand([page(1, "https://github.com/acme/api", "acme/api")]);

    const link = screen.getByRole("link", { name: "Open acme/api" });
    expect(link).toHaveAttribute("href", "https://github.com/acme/api");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("names no site for a page the search returned without an address", async () => {
    await expand([{ index: 1, type: "web", title: "Untitled hit" }]);

    expect(screen.getByText("Untitled hit")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("folds the list away again", async () => {
    await expand([passage(1, "a.md")]);

    await userEvent.click(screen.getByRole("button", { expanded: true }));

    expect(screen.queryByRole("list")).toBeNull();
  });
});

/**
 * A dataset among the sources of an answer (#T22), and what its version is worth
 * now (#T23).
 *
 * An answer about numbers has to be checkable against the numbers it used, so a
 * dataset citation carries the version it was computed from and opens the data
 * at that version - not whatever the data says by the time somebody clicks. When
 * the data has moved on, the message says so and offers the way to the current
 * numbers; the answer itself is replayed, never recomputed.
 */
describe("a dataset citation", () => {
  it("names the dataset, the version it read, and that it is still current", async () => {
    await expand([dataset(1)]);

    expect(screen.getByText("Sales")).toBeInTheDocument();
    expect(screen.getByText("v5 · latest")).toBeInTheDocument();
  });

  it("opens the data at that version, rather than the current one", async () => {
    datasetMovedOn();
    await expand([dataset(1, { version: 3 })]);

    await userEvent.click(screen.getByRole("button", { name: "Open Sales's data" }));

    expect(push).toHaveBeenCalledWith("/datasets/kb-2?dataset=ds-7&version=3");
  });

  it("opens the data from the row itself, for a reader who does not aim", async () => {
    datasetMovedOn();
    await expand([dataset(1, { version: 3 })]);

    await userEvent.click(screen.getByRole("button", { name: /^Sales/ }));

    expect(push).toHaveBeenCalledWith("/datasets/kb-2?dataset=ds-7&version=3");
  });

  it("marks it as a table, not as a file", () => {
    const { container } = render(<MessageSources sources={[dataset(1)]} onOpen={vi.fn()} />);

    expect(container.querySelectorAll(".lucide-table-2")).toHaveLength(1);
    expect(container.querySelectorAll("[class*=lucide-file]")).toHaveLength(0);
  });

  it("still shows one recorded before citations carried ids, and opens nothing", async () => {
    // A conversation from before datasets were linkable holds only the name.
    await expand([dataset(1, { datasetId: undefined, baseId: undefined })]);

    expect(screen.getByText("Sales")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Open Sales/ })).toBeNull();
    expect(screen.queryByTestId(/dataset-notice/)).toBeNull();
  });
});

describe("an answer whose data has moved on", () => {
  it("says nothing when the version it read is still current", () => {
    render(<MessageSources sources={[dataset(1)]} onOpen={vi.fn()} />);

    expect(screen.queryByTestId(/dataset-notice/)).toBeNull();
  });

  it("warns, naming both versions, rather than rewriting the answer", () => {
    datasetMovedOn();
    render(<MessageSources sources={[dataset(1, { version: 3 })]} onOpen={vi.fn()} />);

    const notice = screen.getByTestId("dataset-notice-moved");
    expect(notice).toHaveTextContent(/moved on to v5/);
    expect(notice).toHaveTextContent(/read v3/);
  });

  it("points the citation at the latest data on request, and back again", async () => {
    datasetMovedOn();
    render(<MessageSources sources={[dataset(1, { version: 3 })]} onOpen={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { expanded: false }));

    await userEvent.click(screen.getByRole("button", { name: "View the latest" }));

    // A view state: the citation now reads the current version, the message does
    // not.
    expect(screen.getByText("v5 · latest")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Back to v3" }));
    expect(screen.getByText("v3")).toBeInTheDocument();
  });

  it("turns red when records were deleted after the answer was computed", () => {
    datasetMovedOn();
    server.operations = [deletion(4), deletion(2)];
    render(<MessageSources sources={[dataset(1, { version: 3 })]} onOpen={vi.fn()} />);

    const notice = screen.getByTestId("dataset-notice-deleted");
    expect(notice).toHaveTextContent(/1 record deleted after v3/);
  });

  it("calls a version that is no longer kept archived", () => {
    server.datasets = [sales()];
    server.versions = [version(5), version(4)];
    server.total = 2;
    render(<MessageSources sources={[dataset(1, { version: 3 })]} onOpen={vi.fn()} />);

    expect(screen.getByTestId("dataset-notice-archived")).toHaveTextContent(/v3 is archived/);
  });

  it("says the dataset is gone, and keeps the answer readable", async () => {
    server.datasets = [];
    await expand([dataset(1, { version: 3 })]);

    expect(screen.getByTestId("dataset-notice-gone")).toHaveTextContent(/is gone/);
    expect(screen.getByText("Sales")).toBeInTheDocument();
  });

  it("holds its tongue once the reader has said they know", async () => {
    datasetMovedOn();
    render(<MessageSources sources={[dataset(1, { version: 3 })]} onOpen={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Keep v3" }));

    expect(screen.queryByTestId(/dataset-notice/)).toBeNull();
  });
});
