import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";

import en from "../../messages/en.json";
import { datasetPath, extractSources, sourceDocuments } from "./chat-sources";
import type { Translate } from "@/lib/agent-step-captions";
import type { ChatMessage, ToolCall } from "@/types";

/** The real `chat` messages, so `chunk {number}` is asserted as it renders. */
const t = createTranslator({ locale: "en", messages: en, namespace: "chat" }) as Translate;

function toolCall(overrides: Partial<ToolCall> = {}): ToolCall {
  return {
    id: "tc-1",
    name: "search_documents",
    args: {},
    result: "",
    status: "completed",
    ...overrides,
  };
}

function message(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "m-1",
    role: "assistant",
    content: "Here is what I found.",
    timestamp: new Date("2026-07-31T12:00:00Z"),
    ...overrides,
  };
}

/** What the knowledge tool actually writes back, verbatim in shape. */
const RAG_RESULT = [
  "[1] Source: handbook.pdf, page 4, chunk 2 [handbook] (score: 0.812)",
  "Refunds are granted within thirty days.",
  "[2] Source: policy.md (score: 0.640)",
  "Gift cards are excluded.",
].join("\n");

const WEB_RESULT = JSON.stringify({
  kind: "web_search",
  query: "refund law",
  results: [
    { title: "Consumer rights", url: "https://www.gov.example/rights", content: "Fourteen days." },
    { title: "", url: "https://acme.example/help", content: "Our own policy." },
  ],
});

/**
 * The citation list under an answer.
 *
 * It is derived rather than stored: the backend records a tool call and its raw
 * text, and this is what turns that into things somebody can click. Two rules
 * are load-bearing - a source with no readable result is skipped rather than
 * rendered as an empty card, and the parts timeline wins over the flat
 * `toolCalls` array, because a replayed conversation has both and only the parts
 * are ordered.
 */
describe("extractSources", () => {
  it("reads the knowledge tool's own result format", () => {
    const sources = extractSources(message({ toolCalls: [toolCall({ result: RAG_RESULT })] }), t);

    expect(sources).toHaveLength(2);
    expect(sources[0]).toMatchObject({
      index: 1,
      type: "rag",
      title: "handbook.pdf",
      subtitle: "p.4 · chunk 2",
      score: 0.812,
    });
  });

  it("leaves the subtitle off a passage with neither a page nor a chunk", () => {
    // A Markdown file has neither; " · " on its own reads as a missing value.
    const sources = extractSources(message({ toolCalls: [toolCall({ result: RAG_RESULT })] }), t);

    expect(sources[1]).toMatchObject({ title: "policy.md" });
    expect(sources[1]?.subtitle).toBeUndefined();
  });

  it("names the section a page-less passage opens with (#2489)", () => {
    const result =
      '[1] Source: guide.md, section "Install", chunk 3 [docs] (score: 0.700)\nRun it.';
    const sources = extractSources(message({ toolCalls: [toolCall({ result })] }), t);

    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({ title: "guide.md", subtitle: "Install · chunk 3" });
  });

  it("reads web hits, naming each by its domain", () => {
    const sources = extractSources(
      message({ toolCalls: [toolCall({ name: "web_search", result: WEB_RESULT })] }),
      t,
    );

    expect(sources[0]).toMatchObject({
      index: 1,
      type: "web",
      title: "Consumer rights",
      subtitle: "gov.example",
      url: "https://www.gov.example/rights",
    });
  });

  it("falls back to the domain for a hit with no title", () => {
    const sources = extractSources(
      message({ toolCalls: [toolCall({ name: "web_search", result: WEB_RESULT })] }),
      t,
    );

    expect(sources[1]?.title).toBe("acme.example");
  });

  it("shows an unparsable URL as it arrived rather than dropping the hit", () => {
    // A provider occasionally returns a relative or malformed URL; the passage is
    // still worth citing, and `new URL` would otherwise throw inside the loop and
    // lose every source after it.
    const malformed = JSON.stringify({
      kind: "web_search",
      query: "refunds",
      results: [{ title: "", url: "acme.example/help", content: "text" }],
    });

    const sources = extractSources(
      message({ toolCalls: [toolCall({ name: "web_search", result: malformed })] }),
      t,
    );

    expect(sources[0]).toMatchObject({ title: "acme.example/help", subtitle: "acme.example/help" });
  });

  it("prefers the ordered timeline over the flat list, when a message has both", () => {
    // A replayed conversation carries both; only `parts` says what ran when.
    const sources = extractSources(
      message({
        parts: [
          { id: "p-1", type: "tool", toolCall: toolCall({ result: RAG_RESULT }) },
          { id: "p-2", type: "text", content: "Here is what I found." },
        ],
        toolCalls: [toolCall({ name: "web_search", result: WEB_RESULT })],
      }),
      t,
    );

    expect(sources.every((source) => source.type === "rag")).toBe(true);
  });

  it("skips a tool call that has no result yet", () => {
    // Which is every tool call while it is still running.
    expect(extractSources(message({ toolCalls: [toolCall({ result: undefined })] }), t)).toEqual(
      [],
    );
  });

  it("skips a tool whose result is not text", () => {
    // A structured result belongs to a renderer, not to the citation list.
    expect(extractSources(message({ toolCalls: [toolCall({ result: { rows: 3 } })] }), t)).toEqual(
      [],
    );
  });

  it("cites nothing for a tool that produces no sources", () => {
    expect(
      extractSources(message({ toolCalls: [toolCall({ name: "run_python", result: "42" })] }), t),
    ).toEqual([]);
  });

  it("cites nothing for a web result that is not the payload it claims to be", () => {
    // A provider error is returned as text on the same tool.
    expect(
      extractSources(
        message({ toolCalls: [toolCall({ name: "web_search", result: "upstream refused" })] }),
        t,
      ),
    ).toEqual([]);
  });

  it("cites nothing for a message that called no tools at all", () => {
    expect(extractSources(message(), t)).toEqual([]);
  });
});

describe("a passage found twice", () => {
  it("is cited once, when an agent searched again and got it back", () => {
    const sources = extractSources(
      message({
        toolCalls: [
          toolCall({ id: "a", result: RAG_RESULT }),
          toolCall({ id: "b", result: RAG_RESULT }),
          toolCall({ id: "c", name: "web_search", result: WEB_RESULT }),
          toolCall({ id: "d", name: "web_search", result: WEB_RESULT }),
        ],
      }),
      t,
    );

    expect(sources).toHaveLength(4);
  });
});

/** What the dataset tools write back, verbatim in shape (#T22). */
const QUERY_RESULT = JSON.stringify({
  dataset_id: "ds-7",
  kb_id: "kb-2",
  dataset: "销售明细",
  version_vector: { 销售明细: 5 },
  columns: ["region", "revenue"],
  rows: [{ region: "华东", revenue: 12 }],
  row_count: 1,
  truncated: false,
  warnings: [],
});

const COMPARE_RESULT = JSON.stringify({
  dataset_id: "ds-7",
  kb_id: "kb-2",
  dataset: "销售明细",
  from_version: 3,
  to_version: 5,
  added: 2,
  changed: 1,
  removed: 0,
  missing: 0,
  unchanged: 40,
  rows: [],
  truncated: false,
});

/**
 * A dataset citation.
 *
 * The tool answers in JSON and names the dataset by id and the version it read,
 * which is what a citation needs: the console opens the data the answer was
 * computed from, not the data as it stands today.
 */
describe("a dataset an answer read", () => {
  it("is cited by name, at the version the query read", () => {
    const sources = extractSources(
      message({ toolCalls: [toolCall({ name: "query_dataset", result: QUERY_RESULT })] }),
      t,
    );

    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({
      type: "dataset",
      title: "销售明细",
      subtitle: "v5",
      datasetId: "ds-7",
      baseId: "kb-2",
      version: 5,
      // The columns read are what a dataset citation says instead of a passage.
      content: "region · revenue",
    });
  });

  it("is cited at the version a comparison compared to", () => {
    const sources = extractSources(
      message({
        toolCalls: [toolCall({ name: "compare_dataset_versions", result: COMPARE_RESULT })],
      }),
      t,
    );

    expect(sources[0]).toMatchObject({ title: "销售明细", version: 5, subtitle: "v5" });
  });

  it("reads the version of its own dataset out of a joined query's vector", () => {
    // A query may join another dataset in, and the vector names the version of
    // each - the answer still belongs to the dataset that was asked.
    const joined = JSON.stringify({
      dataset_id: "ds-7",
      kb_id: "kb-2",
      dataset: "销售明细",
      version_vector: { 客户: 9, 销售明细: 4 },
      columns: [],
      rows: [],
      row_count: 0,
    });
    const sources = extractSources(
      message({ toolCalls: [toolCall({ name: "query_dataset", result: joined })] }),
      t,
    );

    expect(sources[0]?.version).toBe(4);
  });

  it("cites nothing for a dataset tool that refused the query", () => {
    // Which it does in prose, on the same tool.
    const sources = extractSources(
      message({
        toolCalls: [
          toolCall({
            name: "query_dataset",
            result: "The dataset refused that query: no such field",
          }),
        ],
      }),
      t,
    );

    expect(sources).toEqual([]);
  });

  it("is cited again when the same dataset is read at another version", () => {
    const later = JSON.stringify({
      dataset_id: "ds-7",
      kb_id: "kb-2",
      dataset: "销售明细",
      version_vector: { 销售明细: 6 },
      columns: ["region", "revenue"],
      rows: [],
      row_count: 0,
    });
    const sources = extractSources(
      message({
        toolCalls: [
          toolCall({ id: "a", name: "query_dataset", result: QUERY_RESULT }),
          toolCall({ id: "b", name: "query_dataset", result: later }),
        ],
      }),
      t,
    );

    // Two answers from two versions of the data are two citations; the card the
    // stack draws still counts one dataset, at the newer version.
    expect(sources).toHaveLength(2);
    expect(sourceDocuments(sources).map((doc) => [doc.title, doc.version])).toEqual([
      ["销售明细", 6],
    ]);
  });

  it("is one document however many times it was queried at that version", () => {
    const docs = sourceDocuments(
      extractSources(
        message({
          toolCalls: [
            toolCall({ id: "a", name: "query_dataset", result: QUERY_RESULT }),
            toolCall({ id: "b", name: "query_dataset", result: QUERY_RESULT }),
          ],
        }),
        t,
      ),
    );

    expect(docs).toHaveLength(1);
  });
});

describe("datasetPath", () => {
  it("opens the dataset in its base, as of the version read", () => {
    expect(datasetPath({ baseId: "kb-2", datasetId: "ds-7", version: 5 })).toBe(
      "/datasets/kb-2?dataset=ds-7&version=5",
    );
  });

  it("leaves the version out when the read was of whatever was current", () => {
    expect(datasetPath({ baseId: "kb-2", datasetId: "ds-7" })).toBe("/datasets/kb-2?dataset=ds-7");
  });

  it("has nowhere to open for a citation recorded before ids were carried", () => {
    // A conversation from before #T22 holds only the dataset's name.
    expect(datasetPath({})).toBeNull();
    expect(datasetPath({ baseId: "kb-2" })).toBeNull();
    expect(datasetPath({ datasetId: "ds-7" })).toBeNull();
  });
});

/** Passages become the documents a reader counts: one per file, one per page. */
describe("sourceDocuments", () => {
  it("counts a file's passages once, under the first citation of it", () => {
    const docs = sourceDocuments([
      { index: 1, type: "rag", title: "refunds.md", collection: "support" },
      { index: 2, type: "web", title: "Rights", url: "https://gov.example/rights" },
      { index: 3, type: "rag", title: "refunds.md", collection: "support" },
      // The same name in another base is another file.
      { index: 4, type: "rag", title: "refunds.md", collection: "finance" },
    ]);

    expect(docs.map((d) => [d.index, d.passages])).toEqual([
      [1, 2],
      [2, 1],
      [4, 1],
    ]);
  });

  it("tells pages apart by address, and one without an address by its title", () => {
    const docs = sourceDocuments([
      { index: 1, type: "web", title: "A" },
      { index: 2, type: "web", title: "A" },
    ]);

    expect(docs).toHaveLength(1);
    expect(docs[0]?.key).toBe("web:A");
  });
});
