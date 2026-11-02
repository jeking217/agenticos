"use client";

import { CopyButton } from "../copy-button";
import { DocumentCard } from "../document-card";
import { officeNamedIn } from "@/lib/turn-documents";
import { contentArg, pathArg } from "@/lib/tool-steps";
import type { ToolCall } from "@/types";
import { useTranslations } from "next-intl";

export { isWorkspaceTool } from "@/lib/tool-catalog";

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** A listing's output split into lines, or null when it is not one. */
function lines(name: string, result: string): string[] | null {
  if (!["ls", "glob", "grep"].includes(name)) return null;
  const found = result
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return found.length === 0 ? null : found;
}

/**
 * What a workspace tool did, shown as the thing it did.
 *
 * The step above says *Wrote test1.md*; this is what opens under it. Four shapes,
 * one per kind of call, because the calls are not alike: a write ends in a file, a
 * read ends in text, a listing ends in a list, and a command ends in output that has
 * to keep its own line breaks.
 *
 * The raw arguments are one click away in the step's own header, which is where
 * somebody debugging a call looks - and is why nothing here needs to be exhaustive.
 */
export function WorkspaceToolResult({
  toolCall,
  resultText,
  conversationId,
}: {
  toolCall: ToolCall;
  resultText: string;
  conversationId?: string;
}) {
  const t = useTranslations("chat.tools");
  const path = pathArg(toolCall.args);
  const written = contentArg(toolCall.args);
  const listed = lines(toolCall.name, resultText);
  const command = asString(toolCall.args?.command) ?? asString(toolCall.args?.cmd);
  const isRunning = toolCall.status === "running" || toolCall.status === "pending";
  const isError = toolCall.status === "error";
  const finished = !isRunning && toolCall.status !== "awaiting_approval";
  const isWrite = toolCall.name === "write_file" || toolCall.name === "edit_file";
  /**
   * The file this call left behind: the one it says it wrote, or the one a command
   * says it produced. Nothing while the call is in flight or after it failed - a card
   * offering to open a file that was never written is the one wrong thing this could
   * do.
   *
   * A command's own text is searched even while the step is closed: `office.py deck
   * spec.json /workspace/Q3.pptx` names its output, and the step draws the deck from
   * the command alone rather than after someone opens it.
   */
  const produced =
    finished && !isError
      ? isWrite && path !== null
        ? path
        : officeNamedIn(`${command ?? ""}\n${resultText}`)
      : null;
  const showsCard = produced !== null;

  return (
    <div className="space-y-2">
      {/* The file itself, once there is one. Not while the call is in flight and not
          when it failed: a card offering to open a file that was never written is the
          one wrong thing this could do.
          With its pages: while the turn runs, this step is the only place a deck being
          built can be watched - the panel under the answer arrives with the answer.
          The same file is drawn again there afterwards, and that is not a duplicate
          but the two halves of it: the work folded above, the deliverable below. */}
      {produced !== null && (
        <DocumentCard conversationId={conversationId} path={produced} preview />
      )}

      {command !== null && (
        <pre className="bg-muted text-foreground/80 overflow-x-auto rounded-md px-3 py-2 font-mono text-xs">
          <span className="text-muted-foreground select-none">$ </span>
          {command}
        </pre>
      )}

      {/* What was put into the file, and not when a card already offers the file.
          Beside a card that names `main.py` and opens it, the whole contents pasted
          underneath is the same thing told worse: it pushes the rest of the turn off
          the screen, and what it duplicates is one click away in the card and one
          more in the step's raw view.
          Only a *write* is folded away. An edit keeps its text even with a card,
          because the argument is the replacement rather than the file - a diff by
          intent, which the card cannot say and opening the file does not show. And
          both keep it when there is no card at all: for a failed write or one still
          in flight, the argument is the only evidence of what was attempted. */}
      {written !== null && !(showsCard && toolCall.name === "write_file") && (
        <TextPanel text={written} />
      )}

      {/* What a read answered, which is the file. Kept out of the muted one-liner
          below because it is the point of the call, not a status. */}
      {finished && toolCall.name === "read_file" && resultText !== "" && (
        <TextPanel text={resultText} />
      )}

      {isRunning && <p className="text-muted-foreground text-xs italic">{t("running")}</p>}

      {/* A listing is a list. Fifty paths in a `pre` is a wall; fifty rows is
          something an eye can scan for the one it wanted. */}
      {finished && listed !== null && (
        <ul className="divide-border/60 divide-y">
          {listed.slice(0, 50).map((line) => (
            <li key={line} className="truncate py-1 font-mono text-xs">
              {line}
            </li>
          ))}
          {listed.length > 50 && (
            <li className="text-muted-foreground py-1 text-xs">
              {t("moreEntries", { count: listed.length - 50 })}
            </li>
          )}
        </ul>
      )}

      {/* A command's output, which is neither a list nor a file: it is a terminal's,
          and folding its line breaks away would make a stack trace unreadable. */}
      {finished && toolCall.name === "execute" && resultText !== "" && (
        <pre className="bg-foreground/[0.04] max-h-64 overflow-auto rounded-md p-3 font-mono text-xs whitespace-pre">
          {resultText}
        </pre>
      )}

      {/* Whatever else the tool said back - and not when the card above already said
          it. `write_file` answers "Wrote 1 lines to /workspace/test1.md", which beside
          a card naming the file is the same fact told worse. It stays for the cases
          with no card: a failure, or a path the arguments did not carry. */}
      {finished &&
        listed === null &&
        !showsCard &&
        !["read_file", "execute"].includes(toolCall.name) &&
        resultText !== "" && (
          <p
            className={
              isError ? "text-destructive text-xs" : "text-muted-foreground text-xs italic"
            }
          >
            {resultText.length > 400 ? `${resultText.slice(0, 400)}…` : resultText}
          </p>
        )}
    </div>
  );
}

/** A body of text with a way to take it, which is what somebody wants from one. */
function TextPanel({ text }: { text: string }) {
  return (
    <div className="relative">
      <pre className="bg-muted max-h-64 overflow-auto rounded-md p-3 pr-9 text-xs whitespace-pre-wrap">
        {text}
      </pre>
      <CopyButton text={text} className="absolute top-1 right-1 h-6 w-6 rounded-md" />
    </div>
  );
}
