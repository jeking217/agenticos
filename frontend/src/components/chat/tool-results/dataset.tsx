/**
 * What one dataset tool call read.
 *
 * The four dataset tools answer in JSON, and three of them say which dataset
 * they touched and at which version. That pair is the whole point of recording
 * the call: it is what lets a citation open the data the answer was computed
 * from rather than whatever the data says today (#T22).
 *
 * Parsed here rather than in the citation list because a tool that refused the
 * query answers in prose - "The dataset refused that query: ..." - and prose on
 * this tool is not a source.
 */

/** The tools whose answer is a read of one dataset, at one version. */
const READS = new Set(["query_dataset", "compare_dataset_versions"]);

export interface DatasetRead {
  /** The dataset's id, which the console opens it by. */
  id?: string;
  /** The base the dataset lives in, which is the page that shows it. */
  baseId?: string;
  name: string;
  /** The version of the data the answer was computed from. */
  version?: number;
  /** Rows the query returned. A comparison returns a diff, not rows. */
  rows?: number;
  columns: string[];
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

function count(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function parseDatasetRead(tool: string, result: string): DatasetRead | null {
  if (!READS.has(tool)) return null;

  let payload: Record<string, unknown> | null;
  try {
    payload = record(JSON.parse(result));
  } catch {
    return null;
  }
  if (!payload) return null;

  const name = text(payload.dataset);
  if (!name) return null;

  // A query names the version of every dataset it read, keyed by name, because a
  // query may join another dataset in. The answer is its own dataset's line.
  const vector = record(payload.version_vector);
  const version =
    tool === "compare_dataset_versions" ? count(payload.to_version) : count(vector?.[name]);

  return {
    id: text(payload.dataset_id),
    baseId: text(payload.kb_id),
    name,
    version,
    rows: count(payload.row_count),
    columns: Array.isArray(payload.columns)
      ? payload.columns.filter((column): column is string => typeof column === "string")
      : [],
  };
}
