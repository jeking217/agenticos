"use client";

import { useState } from "react";
import { Download } from "lucide-react";
import { useTranslations } from "next-intl";

import { FileIcon, FileViewer, OfficePageGrid } from "@/components/files";
import { Button } from "@/components/ui";
import { useConversationWorkspace, useFileActions } from "@/hooks";
import type { FileAccess } from "@/lib/file-access";
import { resolveFileKind, suffixOf } from "@/lib/file-kinds";
import { officeFormatOf } from "@/lib/office-document";
import { basename } from "@/lib/tool-steps";
import { cn, formatBytes } from "@/lib/utils";
import { workspaceFileAccess, type FileSource } from "@/lib/workspace-files";

interface DocumentCardProps {
  /** The conversation whose workspace holds the file. Absent on a surface with none. */
  conversationId?: string;
  /**
   * The path the call named - `Q3.pptx`, `/workspace/Q3.pptx`, `/tmp/Q3.pptx`.
   *
   * Absolute or bare, it is only a *hint*: what a person can open is what the
   * conversation's workspace lists, so the name is matched against the listing and
   * the listing's own spelling is what gets read.
   */
  path: string;
  /** Draw the pages under the card. Off inside a step, which has its own line. */
  preview?: boolean;
  className?: string;
}

/**
 * A file a turn produced, as something to open rather than a sentence about it.
 *
 * `write_file` answers "Wrote 1 lines to /workspace/test1.md", which is true and is
 * not what somebody wants from a turn that just made them a document. The card is the
 * end of that turn: what it is called, what kind of thing it is, and the two things
 * anybody does next.
 *
 * **The path is preferred from the conversation's own listing, and the argument is
 * the fallback.** A tool is called with `test1.md` and answers about
 * `/workspace/test1.md`, while the workspace lists it under whichever of those its
 * backend stored - so matching the listing by name is what makes Open and Download
 * open the right thing.
 *
 * What it must not do is *withhold* the controls when the listing has not caught up.
 * It used to, and the listing is behind for the whole of the interesting case: a
 * workspace has no committed row until a turn flushes one, so a turn that writes a
 * file and then parks for approval showed a card with no way to open the file it had
 * just made - until a page reload, at which point both buttons appeared. The card is
 * drawn because the write *succeeded*; that is the same evidence the buttons need.
 * If the path turns out to be wrong the viewer says so in a sentence, which is a far
 * better answer than a control that was never offered.
 */
export function DocumentCard({
  conversationId,
  path,
  preview = false,
  className,
}: DocumentCardProps) {
  const t = useTranslations("files");
  const { workspace } = useConversationWorkspace(conversationId ?? null);
  // The page to open on, once there is a page to pick: null is closed.
  const [opened, setOpened] = useState<number | null>(null);
  const name = basename(path);
  const format = officeFormatOf(name);
  const suffix = suffixOf(name);
  const entry =
    workspace?.items.find((file) => !file.is_dir && file.path === path) ??
    workspace?.items.find((file) => !file.is_dir && basename(file.path) === name) ??
    null;
  const source: FileSource | null =
    conversationId === undefined ? null : { kind: "conversation", id: conversationId };
  // The listing's path when it has one, the tool's argument when it does not. Only
  // the conversation is required, because that is what the file routes address.
  const target = entry?.path ?? path;
  const access = source === null ? null : workspaceFileAccess(source, target);
  const size = entry?.size != null && entry.size > 0 ? formatBytes(entry.size) : null;

  return (
    <div className={cn("space-y-2", className)}>
      <div className="border-foreground/12 flex items-center gap-3 rounded-xl border p-2.5">
        <span className="bg-foreground/5 text-foreground/60 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
          <FileIcon name={path} className="h-4 w-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{name}</span>
          <span className="text-muted-foreground text-xs">
            {t(`kinds.${resolveFileKind(path)}`)}
            {suffix !== "" && <> · {suffix.toUpperCase()}</>}
            {size !== null && <> · {size}</>}
          </span>
        </span>
        {access !== null && (
          <FileCardActions access={access} name={name} onOpen={() => setOpened(0)} />
        )}
        {opened !== null && access !== null && (
          <FileViewer
            file={{
              name,
              path: target,
              size: entry?.size ?? null,
              modifiedAt: entry?.modified_at ?? null,
            }}
            access={access}
            initialPage={opened}
            onClose={() => setOpened(null)}
          />
        )}
      </div>
      {/* What a deck or a report actually looks like, under the line that says one
          was written: three pages to a row, at a size the page can be recognised at.
          Silent for every other kind - a chart does not have pages. */}
      {preview && format !== null && access !== null && (
        <OfficePageGrid
          access={access}
          name={name}
          format={format}
          onOpen={(page) => setOpened(page ?? 0)}
        />
      )}
    </div>
  );
}

function FileCardActions({
  access,
  name,
  onOpen,
}: {
  access: FileAccess;
  name: string;
  onOpen: () => void;
}) {
  const t = useTranslations("files");
  const tc = useTranslations("common");
  const { download, error } = useFileActions(access);

  return (
    <span className="flex shrink-0 items-center gap-1">
      {error !== null && <span className="text-destructive mr-1 text-xs">{error}</span>}
      <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={onOpen}>
        {t("open")}
      </Button>
      <Button
        variant="outline"
        size="sm"
        className="h-7 text-xs"
        aria-label={tc("downloadNamed", { name })}
        onClick={download}
      >
        <Download className="h-3.5 w-3.5" />
        {t("download")}
      </Button>
    </span>
  );
}
