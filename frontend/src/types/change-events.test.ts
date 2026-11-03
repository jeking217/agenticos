import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { staleQueries } from "@/lib/live-updates";
import type { ChangeEvent, ChangeResource } from "@/types/change-events";

/**
 * The two ends of the change feed, held against each other.
 *
 * The socket's vocabulary is defined on the server (`ChangeResource` in
 * `backend/app/schemas/change_event.py`) and mirrored here. Nothing in either
 * build reads the other's list, so a resource added on one side only is a silent
 * mismatch until it is exercised - and the console's first `dataset` event was:
 * `STALE[event.resource]` was not a function, which took the whole handler down
 * and reported a runtime error on the page that had just published a workbook.
 * The compiler cannot see the server's list, so this reads both files.
 */

const REPO = join(__dirname, "..", "..", "..");
const SCHEMA = readFileSync(join(REPO, "backend", "app", "schemas", "change_event.py"), "utf8");
const CONSOLE = readFileSync(join(__dirname, "change-events.ts"), "utf8");

function quoted(text: string): string[] {
  return [...text.matchAll(/"([a-z_]+)"/g)].flatMap((match) => (match[1] ? [match[1]] : []));
}

/** The text a capture group matched, refusing to treat "absent" as empty. */
function captured(match: RegExpMatchArray, name: string): string {
  const value = match[1];
  if (value === undefined) throw new Error(`${name} no longer has a body to read`);
  return value;
}

/** What the server can put in an event's `resource`. */
function resourcesTheServerNames(): string[] {
  const literal = SCHEMA.match(/ChangeResource = Literal\[([\s\S]*?)\]/);
  if (!literal) throw new Error("ChangeResource is no longer a Literal in change_event.py");
  return quoted(captured(literal, "ChangeResource"));
}

/** What this console admits it may hear. */
function resourcesTheConsoleNames(): string[] {
  const union = CONSOLE.match(/export type ChangeResource =([\s\S]*?);/);
  if (!union) throw new Error("ChangeResource is no longer a union in types/change-events.ts");
  return quoted(captured(union, "types/change-events.ts"));
}

function event(resource: string): ChangeEvent {
  return {
    organization_id: "org-1",
    resource: resource as ChangeResource,
    id: "row-1",
    action: "created",
    surface: "console",
    actor_user_id: "user-1",
    actor_name: "Ada",
    origin_tab: null,
  };
}

describe("the change feed's resources", () => {
  it("names on the console every resource the server can send", () => {
    // A resource absent from the union is one TypeScript will not let this build
    // handle, which is exactly how `dataset` was missed.
    const missing = resourcesTheServerNames().filter(
      (resource) => !resourcesTheConsoleNames().includes(resource),
    );

    expect(missing).toEqual([]);
  });

  it("can say what a change to each of them makes stale", () => {
    // The behaviour, not the type: a resource with no entry in the console's
    // table is one nobody refreshes, and the point of the feed is that they are.
    const unanswered = resourcesTheServerNames().filter(
      (resource) => staleQueries(event(resource)).length === 0,
    );

    expect(unanswered).toEqual([]);
  });
});
