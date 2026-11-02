/**
 * The documents a turn left behind, by the name each is known by afterwards.
 *
 * A deck is not written by `write_file`: it is written by a script the agent ran,
 * and the step that ran it says what it made only in the command it typed and the
 * one line the script printed back. Both are read here - so a turn that produced a
 * file can offer the file, rather than a sentence naming a path for somebody to go
 * and find.
 *
 * Shared by the step that draws one call and the panel that draws the whole turn,
 * so the two cannot disagree about what was made.
 *
 * Dependency-free apart from the format table, which is the point: *what makes a
 * name an office document* is answered once, in `office-document.ts`, and this file
 * only looks for such names.
 */

import type { ToolCall } from "@/types";
import { officeFormatOf, type OfficeFormat } from "./office-document";
import { basename } from "./tool-steps";

/** One document a turn produced. */
export interface ProducedDocument {
  /** As the person calls it: the last segment of the path the call named. */
  readonly name: string;
  readonly format: OfficeFormat;
}

/**
 * How many are worth drawing under one answer.
 *
 * A turn that builds a deck, a sheet and a document has three things to hand over;
 * a turn that lists forty of them is a batch job, and forty previews above the
 * person's next question is not a summary of it.
 */
const MAX_DOCUMENTS = 3;

/** Words long enough to be a name, and not so long that a whole command is one. */
const QUOTED = /"([^"\n]{1,200})"|'([^'\n]{1,200})'/g;
const TOKEN = /[^\s"'<>|]{1,300}/g;

/**
 * The first office document named in a piece of text, as a name.
 *
 * Quoted spans are read first, and that is not a detail: the decks people ask for
 * have spaces in their names - `Q3 report.pptx` is three tokens otherwise - and a
 * command that writes one always quotes it. Anything left is a path, which is how
 * `/tmp/blue-shan-app.pptx (53217 bytes)` arrives: the byte count sits beside it and
 * the path is the token.
 *
 * The *name* comes back rather than the path, because a workspace listing gives a
 * file by the name it has inside the workspace - `/workspace/Q3.pptx` on disk is
 * `Q3.pptx` in the panel - and matching on the last segment is what makes the two
 * line up.
 */
export function officeNamedIn(text: string): string | null {
  if (text === "") return null;
  for (const match of text.matchAll(QUOTED)) {
    const named = match[1] ?? match[2];
    if (named !== undefined && officeFormatOf(basename(named)) !== null) return basename(named);
  }
  for (const match of text.matchAll(TOKEN)) {
    const token = match[0];
    if (officeFormatOf(basename(token)) !== null) return basename(token);
  }
  return null;
}

/** The file a call wrote, when the call says so in its own argument. */
function writtenPath(call: ToolCall): string | null {
  if (call.name !== "write_file" && call.name !== "edit_file") return null;
  const given = call.args?.path;
  const path = typeof given === "string" ? given : null;
  return path !== null && officeFormatOf(basename(path)) !== null ? basename(path) : null;
}

/** What a command named, in the command it ran and the line it printed. */
function commandText(call: ToolCall): string {
  const args = call.args ?? {};
  const command = args.command ?? args.cmd;
  const ran = typeof command === "string" ? command : "";
  const printed =
    typeof call.result === "string"
      ? call.result
      : call.result === undefined
        ? ""
        : JSON.stringify(call.result);
  return `${ran}\n${printed}`;
}

/**
 * The name this call produced, or null.
 *
 * Only two shapes make a document: a write of one, and a command that built one -
 * `execute` is the whole point of this file, because the office script is how a deck
 * is actually built. Nothing else is read, and that is not a shortcut: `read_file` of
 * a skill's own docs prints a usage line naming `out.pptx`, and counting that made
 * the answer offer a file that was never written.
 */
function producedBy(call: ToolCall): string | null {
  const written = writtenPath(call);
  if (written !== null) return written;
  return call.name === "execute" ? officeNamedIn(commandText(call)) : null;
}

/**
 * What this turn made, newest name wins, oldest order kept.
 *
 * Ordered by first appearance because that is the order the work happened in, and
 * de-duplicated by name because a deck built four times is one deck: the agent
 * rewrote the spec until the script accepted it, and four copies of the same file
 * name under the answer is a record of the retries rather than of the result.
 *
 * Only calls that finished. A file a failed call *meant* to write is not a file, and
 * offering to open it is the one wrong thing this could do.
 */
export function producedDocuments(toolCalls: readonly ToolCall[]): ProducedDocument[] {
  const made = new Map<string, ProducedDocument>();
  for (const call of toolCalls) {
    if (call.status !== "completed") continue;
    const name = producedBy(call);
    if (name === null) continue;
    const format = officeFormatOf(name);
    if (format === null) continue;
    made.set(name, { name, format });
  }
  return [...made.values()].slice(0, MAX_DOCUMENTS);
}
