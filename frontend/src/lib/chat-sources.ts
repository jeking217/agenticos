import { parseDatasetRead } from "@/components/chat/tool-results/dataset";
import { parseRAGResults } from "@/components/chat/tool-results/rag";
import { parseWebSearch } from "@/components/chat/tool-results/web-search";
import type { Translate } from "@/lib/agent-step-captions";
import { hostOf } from "@/lib/link-brands";
import type { ChatMessage, ToolCall } from "@/types";

export interface SourceItem {
  index: number;
  type: "rag" | "web" | "dataset";
  title: string;
  subtitle?: string;
  url?: string;
  content?: string;
  score?: number;
  /** The collection a knowledge base passage came from, which names its base. */
  collection?: string;
  /** The dataset a citation read, and the base it lives in (#T22). */
  datasetId?: string;
  baseId?: string;
  /** The version of that dataset the answer was computed from. */
  version?: number;
}

/** A message's tool calls, from its ordered timeline where it has one. */
export function toolCallsOf(message: ChatMessage): ToolCall[] {
  const fromParts = (message.parts ?? [])
    .filter((p) => p.type === "tool" && !!p.toolCall)
    .map((p) => p.toolCall!);
  if (fromParts.length > 0) return fromParts;
  return message.toolCalls ?? [];
}

export function extractSources(message: ChatMessage, t: Translate): SourceItem[] {
  const sources: SourceItem[] = [];
  // An agent that searches twice is often handed the same passage twice, and a
  // reader counting sources counts it once.
  const seen = new Set<string>();
  const fresh = (source: SourceItem): boolean => {
    // A dataset read again at another version is another source: the two
    // answers were computed from different rows (#T22).
    const identity = `${sourceKey(source)}\n${source.version ?? ""}\n${source.content ?? ""}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  };

  for (const tc of toolCallsOf(message)) {
    const result = typeof tc.result === "string" ? tc.result : "";
    if (!result) continue;

    if (tc.name === "search_documents") {
      for (const item of parseRAGResults(result)) {
        const source: SourceItem = {
          index: item.index,
          type: "rag",
          title: item.source,
          subtitle:
            [
              item.page && `p.${item.page}`,
              item.section,
              item.chunk && t("chunkNumber", { number: item.chunk }),
            ]
              .filter(Boolean)
              .join(" · ") || undefined,
          content: item.content,
          score: item.score ? parseFloat(item.score) : undefined,
          collection: item.collection,
        };
        if (fresh(source)) sources.push(source);
      }
    } else if (tc.name === "web_search") {
      const payload = parseWebSearch(result);
      if (payload?.results) {
        payload.results.forEach((hit, i) => {
          const source: SourceItem = {
            index: i + 1,
            type: "web",
            title: hit.title || (hostOf(hit.url) ?? hit.url),
            subtitle: hostOf(hit.url) ?? hit.url,
            url: hit.url,
            content: hit.content,
          };
          if (fresh(source)) sources.push(source);
        });
      }
    } else {
      const read = parseDatasetRead(tc.name, result);
      if (read) {
        const source: SourceItem = {
          index: sources.length + 1,
          type: "dataset",
          title: read.name,
          subtitle: read.version === undefined ? undefined : `v${read.version}`,
          content: read.columns.join(" · ") || undefined,
          datasetId: read.id,
          baseId: read.baseId,
          version: read.version,
        };
        if (fresh(source)) sources.push(source);
      }
    }
  }

  return sources;
}

/** One document an answer drew on: every passage of a file, or one web page. */
export interface SourceDocument {
  key: string;
  type: SourceItem["type"];
  title: string;
  /** The page's address, for a web source. */
  url?: string;
  collection?: string;
  /** The dataset's ids and the latest version an answer read, for a dataset. */
  datasetId?: string;
  baseId?: string;
  version?: number;
  /** The first passage's index, which the panel opens on. */
  index: number;
  passages: number;
}

/** Which document a source belongs to: a file in a collection, a page, or a dataset. */
export function sourceKey(source: SourceItem): string {
  if (source.type === "dataset") {
    return `dataset:${source.datasetId ?? source.title}`;
  }
  return source.type === "rag"
    ? `rag:${source.collection ?? ""}:${source.title}`
    : `web:${source.url ?? source.title}`;
}

/**
 * Where a dataset citation opens: its base's page, on the dataset, at the
 * version the answer read.
 *
 * The version is a view, not a fact about the dataset - the page reads rows as
 * of it and says so (#T23). Nothing to open when the conversation was recorded
 * before citations carried ids, which is why this answers `null`.
 */
export function datasetPath(doc: {
  baseId?: string;
  datasetId?: string;
  version?: number;
}): string | null {
  if (!doc.baseId || !doc.datasetId) return null;
  const query = new URLSearchParams({ dataset: doc.datasetId });
  if (doc.version !== undefined) query.set("version", String(doc.version));
  return `/datasets/${doc.baseId}?${query.toString()}`;
}

/**
 * The documents behind an answer's sources, in the order they were first cited.
 *
 * A search returns passages, and five passages of one handbook are one source to
 * the person reading - the stack under the answer counts files and pages, not
 * chunks.
 */
export function sourceDocuments(sources: SourceItem[]): SourceDocument[] {
  const documents = new Map<string, SourceDocument>();
  for (const source of sources) {
    const key = sourceKey(source);
    const known = documents.get(key);
    if (known) {
      known.passages += 1;
      // Two reads of one dataset: the card names the newer data. Its passages
      // are reads rather than chunks, and it is the last one that answers
      // "which data did this use" (#T22).
      if (source.version !== undefined && (known.version ?? -1) < source.version) {
        known.version = source.version;
      }
      continue;
    }
    documents.set(key, {
      key,
      type: source.type,
      title: source.title,
      url: source.url,
      collection: source.collection,
      datasetId: source.datasetId,
      baseId: source.baseId,
      version: source.version,
      index: source.index,
      passages: 1,
    });
  }
  return [...documents.values()];
}
