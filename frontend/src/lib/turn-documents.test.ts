import { describe, expect, it } from "vitest";

import { officeNamedIn, producedDocuments } from "./turn-documents";
import type { ToolCall } from "@/types";

/**
 * What a turn made, read back off the calls that made it.
 *
 * The case that matters is the one the product shipped broken: a deck is built by a
 * script, the step that ran it is a shell command, and the only evidence the file
 * exists is the command's own text and the line it printed. A reader that only
 * understood `write_file` found nothing there, so the turn handed over a paragraph
 * naming a path instead of the file.
 */
function call(name: string, args: Record<string, unknown>, result?: unknown): ToolCall {
  return {
    id: `${name}-${String(args.path ?? args.command ?? "")}`,
    name,
    args,
    result,
    status: "completed",
  };
}

describe("officeNamedIn", () => {
  it("reads the output path out of the command that built it", () => {
    expect(
      officeNamedIn(
        "python3 office.py deck /workspace/spec.json /tmp/blue-shan-app.pptx\n/tmp/blue-shan-app.pptx (53217 bytes)",
      ),
    ).toBe("blue-shan-app.pptx");
  });

  it("prefers a quoted name, because the decks people ask for have spaces in them", () => {
    expect(officeNamedIn('soffice --outdir /tmp "2026-Q3 report.pptx"')).toBe(
      "2026-Q3 report.pptx",
    );
  });

  it("finds nothing in a command that made nothing", () => {
    expect(officeNamedIn("ls -la /workspace")).toBeNull();
    expect(officeNamedIn("python3 office.py deck spec.json")).toBeNull();
    expect(officeNamedIn("")).toBeNull();
  });
});

describe("producedDocuments", () => {
  it("reads a deck out of the command that built it", () => {
    const documents = producedDocuments([
      call("execute", {
        command: "python3 office.py deck /workspace/spec.json /workspace/Q3-review.pptx",
      }),
    ]);
    expect(documents).toEqual([{ name: "Q3-review.pptx", format: "deck" }]);
  });

  it("reads all three formats, from a write as well as a command", () => {
    const documents = producedDocuments([
      call("write_file", { path: "/workspace/minutes.docx", content: "…" }),
      call("execute", { command: "python3 office.py sheet spec.json /workspace/numbers.xlsx" }),
    ]);
    expect(documents).toEqual([
      { name: "minutes.docx", format: "doc" },
      { name: "numbers.xlsx", format: "sheet" },
    ]);
  });

  it("counts a deck rebuilt four times as one deck", () => {
    const same = { command: "python3 office.py deck spec.json /workspace/Q3.pptx" };
    const documents = producedDocuments([
      call("execute", same),
      call("execute", same),
      call("execute", same),
    ]);
    expect(documents).toHaveLength(1);
  });

  it("does not offer a file a failed call only meant to write", () => {
    const failed: ToolCall = {
      ...call("execute", { command: "python3 office.py deck spec.json /workspace/Q3.pptx" }),
      status: "error",
      result: "Command failed (exit code 1)",
    };
    expect(producedDocuments([failed])).toEqual([]);
  });

  it("ignores everything that is not one of the three, and every call that is not a build", () => {
    const documents = producedDocuments([
      call("write_file", { path: "/workspace/check.png" }),
      call("write_file", { path: "/workspace/spec.json" }),
      call("execute", { command: "soffice --convert-to pdf /workspace/spec.json" }),
      call("read_file", { path: "/workspace/spec.json" }),
    ]);
    expect(documents).toEqual([]);
  });

  it("does not count a file that a read merely mentioned", () => {
    // Reading the skill's own source prints its usage line - `office.py deck spec.json
    // out.pptx` - and counting that offered a document nobody had built.
    const documents = producedDocuments([
      call(
        "read_file",
        { path: "/workspace/skills/office-docs/office.py" },
        "python3 office.py deck  spec.json out.pptx",
      ),
    ]);
    expect(documents).toEqual([]);
  });

  it("keeps the order the work happened in", () => {
    const documents = producedDocuments([
      call("execute", { command: "python3 office.py deck s.json /workspace/first.pptx" }),
      call("write_file", { path: "/workspace/second.docx" }),
      call("execute", { command: "python3 office.py sheet s.json /workspace/third.xlsx" }),
    ]);
    expect(documents.map((document) => document.name)).toEqual([
      "first.pptx",
      "second.docx",
      "third.xlsx",
    ]);
  });
});
