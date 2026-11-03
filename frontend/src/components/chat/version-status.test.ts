import { describe, expect, it } from "vitest";

import { deletionsSince, versionStatus, versionsForState } from "./version-status";
import type { Dataset, DatasetOperation, DatasetVersion } from "@/types/datasets";

/**
 * What an old answer's version is worth now.
 *
 * The five states of the design are a decision table, and the table is what is
 * worth testing: which of them a reader sees depends on three facts the console
 * learns separately - the dataset's current version, the versions still kept,
 * and whether the dataset is still there at all.
 */

const DATASET = {
  id: "ds-7",
  name: "Sales",
  current_version_no: 5,
} as Dataset;

const version = (no: number, at = "2026-10-15T09:00:00Z") =>
  ({ id: `v-${no}`, version_no: no, published_at: at }) as DatasetVersion;

const base = {
  baseId: "kb-2",
  datasetId: "ds-7",
  version: 3,
  dataset: DATASET,
  datasetsLoaded: true,
};

describe("versionStatus", () => {
  it("has nothing to report for a citation recorded without ids", () => {
    // A conversation from before #T22: a name, and nothing to look up.
    expect(versionStatus({ datasetsLoaded: true })).toEqual({
      state: "unknown",
      version: undefined,
    });
    expect(versionStatus({ ...base, datasetId: undefined })).toMatchObject({ state: "unknown" });
    expect(versionStatus({ ...base, baseId: undefined })).toMatchObject({ state: "unknown" });
    expect(versionStatus({ ...base, version: undefined })).toMatchObject({ state: "unknown" });
  });

  it("waits rather than calling a dataset deleted before the list arrives", () => {
    expect(versionStatus({ ...base, dataset: undefined, datasetsLoaded: false })).toMatchObject({
      state: "pending",
    });
  });

  it("calls it gone once the list has arrived without it", () => {
    // Archived datasets are left out of the list too, and the reader's next move
    // is the same either way: read what the message says.
    expect(versionStatus({ ...base, dataset: undefined, datasetsLoaded: true })).toMatchObject({
      state: "gone",
      version: 3,
    });
  });

  it("says nothing is stale when the answer read the current version", () => {
    expect(versionStatus({ ...base, version: 5, versions: [version(5)] })).toMatchObject({
      state: "current",
      version: 5,
      latest: 5,
    });
  });

  it("says the data has moved on, and when the version it read was published", () => {
    expect(
      versionStatus({ ...base, versions: [version(5), version(4), version(3)] }),
    ).toMatchObject({
      state: "updated",
      version: 3,
      latest: 5,
      publishedAt: "2026-10-15T09:00:00Z",
    });
  });

  it("still says the data has moved on when the versions have not arrived", () => {
    // Half a warning beats none: the version is not current, whatever happened
    // to the old one.
    expect(versionStatus({ ...base, versions: undefined })).toMatchObject({
      state: "updated",
      latest: 5,
    });
    expect(versionStatus({ ...base, versions: undefined }).publishedAt).toBeUndefined();
  });

  it("calls a version that is no longer kept archived", () => {
    expect(versionStatus({ ...base, versions: [version(5), version(4)] })).toMatchObject({
      state: "archived",
      version: 3,
      latest: 5,
    });
  });
});

describe("versionsForState", () => {
  it("trusts a list that reaches the cited version", () => {
    const kept = [version(5), version(3)];
    expect(versionsForState(kept, 100, 3)).toBe(kept);
  });

  it("trusts a short list, which is the whole list", () => {
    const kept = [version(5), version(4)];
    expect(versionsForState(kept, 2, 3)).toBe(kept);
  });

  it("distrusts a full list that stopped short of it", () => {
    // A hundred versions kept and a citation older than the page: saying "it is
    // archived" on that basis would be a guess dressed as a fact.
    const kept = [version(5), version(4)];
    expect(versionsForState(kept, 100, 3)).toBeUndefined();
  });

  it("has nothing to say about a citation without a version", () => {
    expect(versionsForState([version(5)], 1, undefined)).toBeUndefined();
  });
});

describe("deletionsSince", () => {
  const op = (operation: DatasetOperation["operation"], version_no: number) =>
    ({ id: `${operation}-${version_no}`, operation, version_no }) as DatasetOperation;

  it("counts the deletions recorded after the answer read the data", () => {
    // The record the answer was about may be one of them; the operation log is
    // the only place that knows, and it is what the notice links to.
    expect(deletionsSince([op("delete", 4), op("delete", 7), op("update", 6)], 3)).toBe(2);
  });

  it("counts nothing when the deletions came before it", () => {
    expect(deletionsSince([op("delete", 2), op("delete", 3)], 3)).toBe(0);
  });

  it("counts nothing when there is no version to count from", () => {
    expect(deletionsSince([op("delete", 9)], undefined)).toBe(0);
  });
});
