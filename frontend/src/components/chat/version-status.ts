/**
 * Whether an answer still describes the data as it is.
 *
 * A conversation freezes the rows it was given: `query_dataset` writes the
 * versions it read into the tool result, and replaying the message replays those
 * numbers. What changes underneath is everything else - the dataset has moved
 * on, the version was retired, the dataset was archived, or the records the
 * answer was about were deleted afterwards.
 *
 * The states are the design's five (#23 of the plan, N1-N5), with two the
 * interface needs but the design does not draw: `unknown` for a citation
 * recorded before versions were written down, and `pending` while the answer to
 * "does this still exist" is in flight.
 */

import type { Dataset, DatasetOperation, DatasetVersion } from "@/types/datasets";

export type VersionState =
  /** Nothing to compare against: an old citation, or no ids to ask about. */
  | "unknown"
  /** The dataset's own versions are still arriving. */
  | "pending"
  /** N1 - the answer read the version that is still current. */
  | "current"
  /** N2 - the dataset has moved on; the version the answer read survives. */
  | "updated"
  /** N3 - the version is no longer listed: readable if kept, not queryable. */
  | "archived"
  /** N4 - the dataset is gone: deleted, archived, or no longer readable. */
  | "gone";

export interface VersionStatus {
  state: VersionState;
  /** The version the answer was computed from. */
  version?: number;
  /** The dataset's current version, when the dataset is still readable. */
  latest?: number;
  /** When the cited version was published, once the version list says. */
  publishedAt?: string;
  /** Records deleted after the cited version, as far as the log goes. */
  deletedSince?: number;
}

export function versionStatus(input: {
  baseId?: string;
  datasetId?: string;
  version?: number;
  /** The dataset as it stands, once the base's list has loaded. */
  dataset?: Dataset;
  /** Whether that list has arrived - a missing dataset before it has is not gone. */
  datasetsLoaded: boolean;
  /**
   * The versions that still exist. `undefined` when the answer is unknown: still
   * loading, or a list too short to have reached this one.
   */
  versions?: DatasetVersion[];
}): VersionStatus {
  const { datasetId, baseId, version } = input;

  // A conversation recorded before #T22 holds a dataset's name and nothing to
  // ask about; there is no status to report rather than a wrong one.
  if (!datasetId || !baseId || version === undefined) return { state: "unknown", version };

  if (!input.datasetsLoaded) return { state: "pending", version };

  if (!input.dataset) {
    // Archived datasets are left out of the base's list as well, so this covers
    // both "deleted" and "archived" - the reader's next move is the same.
    return { state: "gone", version };
  }

  const latest = input.dataset.current_version_no;
  if (version === latest) return { state: "current", version, latest };

  // The dataset moved on either way. Whether the version the answer read
  // survived is the difference between "read it as it was" and "it is no longer
  // among the versions kept" - and when the list cannot say, say the half that
  // is certain.
  if (!input.versions) return { state: "updated", version, latest };

  const cited = input.versions.find((entry) => entry.version_no === version);
  if (!cited) return { state: "archived", version, latest };
  return { state: "updated", version, latest, publishedAt: cited.published_at };
}

/**
 * The versions a status needs to tell `updated` from `archived`.
 *
 * A list that came back full may simply have stopped short of the cited version,
 * and an answer that says "this version is gone" because a page ended is worse
 * than one that says less.
 */
export function versionsForState(
  versions: DatasetVersion[],
  total: number,
  version: number | undefined,
): DatasetVersion[] | undefined {
  if (version === undefined) return undefined;
  if (versions.some((entry) => entry.version_no === version)) return versions;
  return versions.length >= total ? versions : undefined;
}

/** How many records were deleted after `version`, according to the operation log. */
export function deletionsSince(
  operations: DatasetOperation[],
  version: number | undefined,
): number {
  if (version === undefined) return 0;
  return operations.filter((op) => op.operation === "delete" && op.version_no > version).length;
}
