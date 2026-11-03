"use client";

/**
 * What the data behind a dataset citation looks like now (#T23).
 *
 * Three questions, asked of endpoints that already exist: is the dataset still
 * in its base, which versions has it kept, and what has been deleted since the
 * answer was computed. The answers decide which of the five notices a historical
 * message wears.
 */

import {
  deletionsSince,
  versionStatus,
  versionsForState,
  type VersionStatus,
} from "@/components/chat/version-status";
import { useDatasets, useDatasetVersions, useOperations } from "@/hooks/use-datasets";

export interface CitedDataset {
  baseId?: string;
  datasetId?: string;
  version?: number;
}

export function useDatasetVersionStatus(
  cited: CitedDataset,
  options: { withOperations?: boolean } = {},
): VersionStatus {
  const { baseId, datasetId, version } = cited;

  // Hooks cannot be skipped, so a missing id is an empty one: the queries read
  // `enabled` and stay idle.
  const { datasets, isSuccess: datasetsLoaded } = useDatasets(baseId ?? "");
  const dataset = datasetId ? datasets.find((entry) => entry.id === datasetId) : undefined;

  const { versions, total, isSuccess: versionsLoaded } = useDatasetVersions(datasetId ?? "");
  const movedOn =
    version !== undefined && dataset !== undefined && version !== dataset.current_version_no;

  // The operation log is a second request per citation, and only a stale one has
  // anything to say with it: a current version has nothing deleted "since".
  const { operations } = useOperations(options.withOperations && movedOn ? (datasetId ?? "") : "");

  const status = versionStatus({
    baseId,
    datasetId,
    version,
    dataset,
    datasetsLoaded,
    // An empty list that has not arrived yet is not evidence of anything: it
    // would say "archived" and then change its mind.
    versions: versionsLoaded ? versionsForState(versions, total, version) : undefined,
  });

  return { ...status, deletedSince: deletionsSince(operations, version) };
}
