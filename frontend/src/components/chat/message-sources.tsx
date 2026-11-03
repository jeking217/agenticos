"use client";

import { ChevronDown, ExternalLink, FileSearch, Table2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { SourceViewer, type CitedFile } from "@/components/kb/source-viewer";
import { useDatasetVersionStatus } from "@/hooks/use-dataset-version-status";
import { useKnowledgeBaseFor } from "@/hooks/use-knowledge-bases";
import {
  datasetPath,
  sourceDocuments,
  type SourceDocument,
  type SourceItem,
} from "@/lib/chat-sources";
import { brandForUrl, hostOf } from "@/lib/link-brands";
import { useRouter } from "@/lib/locale-navigation";
import { cn } from "@/lib/utils";

import { SourceMark } from "./source-mark";
import { DatasetNotice } from "./version-stale-banner";

/** Beyond this the stack is a smudge; the rest are counted instead. */
const MAX_SHOWN = 4;

/** What a document's mark looks like: a table, any file, or a page's service. */
function markOf(doc: SourceDocument): string {
  if (doc.type === "dataset") return "dataset";
  return doc.type === "rag" ? "file" : ((doc.url && brandForUrl(doc.url)) ?? "web");
}

/**
 * The documents the stack shows: one of each kind of mark first, then the rest
 * in the order they were cited. Four marks of one handbook's files say less than
 * a file, GitHub and Notion side by side.
 */
function variedFirst(documents: SourceDocument[]): SourceDocument[] {
  const seen = new Set<string>();
  const distinct: SourceDocument[] = [];
  const repeats: SourceDocument[] = [];
  for (const doc of documents) {
    const mark = markOf(doc);
    (seen.has(mark) ? repeats : distinct).push(doc);
    seen.add(mark);
  }
  return [...distinct, ...repeats].slice(0, MAX_SHOWN);
}

/**
 * What an answer drew on, under it: the documents as a stack of marks, opening
 * into a list of them.
 *
 * One mark per document, not per passage - five chunks of a handbook are one
 * source to the reader. The stack tucks its marks under each other and spreads
 * them on hover, as the faces in the conversation list do. A row opens the
 * panel on that source's passages; a knowledge base file can also be opened
 * whole, which is the question a citation raises. A dataset row opens the data
 * at the version the answer read, and says which version that is - an answer
 * about last quarter's numbers is only checkable against those numbers (#T22).
 */
export function MessageSources({
  sources,
  onOpen,
}: {
  sources: SourceItem[];
  onOpen: (index: number | null) => void;
}) {
  const t = useTranslations("chat");
  const [expanded, setExpanded] = useState(false);
  const [opened, setOpened] = useState<CitedFile | null>(null);
  // Which citations are pointed at the latest data instead, and which notices a
  // reader has already said they understood (#T23). Both are view state: the
  // message itself is never rewritten.
  const [viewing, setViewing] = useState<Record<string, boolean>>({});
  const [dismissed, setDismissed] = useState<Record<string, boolean>>({});
  const baseFor = useKnowledgeBaseFor();
  const documents = sourceDocuments(sources);
  const shown = variedFirst(documents);
  const datasets = documents.filter((doc) => doc.type === "dataset" && !dismissed[doc.key]);

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        className="group/stack border-foreground/12 bg-background hover:border-foreground/25 hover:bg-foreground/[0.03] inline-flex items-center gap-2 rounded-full border py-1 pr-2.5 pl-1 transition-colors"
      >
        <span className="flex">
          {shown.map((doc, i) => (
            <SourceMark
              key={doc.key}
              type={doc.type}
              title={doc.title}
              url={doc.url}
              className={cn(
                "transition-[margin] duration-200 ease-out motion-reduce:transition-none",
                i > 0 && ["-ml-1.5", "group-hover/stack:ml-0.5"],
              )}
            />
          ))}
        </span>
        <span className="text-foreground/70 text-xs font-medium">
          {t("sourceCount", { count: documents.length })}
        </span>
        <ChevronDown
          aria-hidden
          className={cn(
            "text-muted-foreground h-3.5 w-3.5 transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>

      {expanded && (
        <ul className="border-foreground/10 divide-foreground/8 max-w-xl divide-y overflow-hidden rounded-xl border">
          {documents.map((doc) =>
            doc.type === "dataset" ? (
              <DatasetRow key={doc.key} doc={doc} viewing={Boolean(viewing[doc.key])} />
            ) : (
              <SourceRow
                key={doc.key}
                doc={doc}
                base={baseFor(doc.collection)?.name}
                onShow={() => onOpen(doc.index)}
                onOpenFile={
                  doc.type === "rag" && baseFor(doc.collection)
                    ? () => setOpened({ kbId: baseFor(doc.collection)!.id, filename: doc.title })
                    : undefined
                }
              />
            ),
          )}
        </ul>
      )}
      {datasets.map((doc) => (
        <DatasetNotice
          key={doc.key}
          doc={doc}
          viewing={Boolean(viewing[doc.key])}
          onView={(latest) => setViewing((current) => ({ ...current, [doc.key]: latest }))}
          onDismiss={() => setDismissed((current) => ({ ...current, [doc.key]: true }))}
        />
      ))}
      {opened && <SourceViewer cited={opened} onClose={() => setOpened(null)} />}
    </div>
  );
}

/**
 * A dataset among the sources: its name, the version being shown, and the way
 * into that version of the data (#T22).
 *
 * The badge follows the notice above it: while a stale citation is pointed at
 * the latest data, this row says so and opens *that* version, which is the whole
 * of what "view the latest" means - the answer above it says what it said.
 */
function DatasetRow({ doc, viewing }: { doc: SourceDocument; viewing: boolean }) {
  const t = useTranslations("chat");
  const router = useRouter();
  const status = useDatasetVersionStatus(doc);
  const version = viewing ? (status.latest ?? doc.version) : doc.version;
  const path = datasetPath({ ...doc, version });
  const suffix =
    viewing || status.state === "current"
      ? t("datasetIsLatest")
      : status.state === "archived"
        ? t("datasetArchived")
        : null;
  const where =
    version === undefined
      ? null
      : [t("datasetVersion", { version }), suffix].filter(Boolean).join(" · ");

  const body = (
    <>
      <p className="text-foreground truncate text-[13px] font-medium" title={doc.title}>
        {doc.title}
      </p>
      <p className="text-muted-foreground truncate text-xs">{where}</p>
    </>
  );

  return (
    <li className="hover:bg-foreground/[0.03] flex items-center gap-2.5 px-3 py-2 transition-colors">
      <SourceMark type="dataset" title={doc.title} />
      {path ? (
        <button
          type="button"
          onClick={() => router.push(path)}
          className="min-w-0 flex-1 text-left"
        >
          {body}
        </button>
      ) : (
        // A citation recorded before dataset tools carried ids: the name is
        // still worth showing, and there is nowhere to send anybody.
        <div className="min-w-0 flex-1">{body}</div>
      )}
      {path && (
        <button
          type="button"
          onClick={() => router.push(path)}
          title={t("openDataset")}
          aria-label={t("openDatasetNamed", { name: doc.title })}
          className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] rounded-md p-1.5"
        >
          <Table2 className="h-4 w-4" />
        </button>
      )}
    </li>
  );
}

function SourceRow({
  doc,
  base,
  onShow,
  onOpenFile,
}: {
  doc: SourceDocument;
  /** The knowledge base a file is in, by the name people know it. */
  base?: string;
  onShow: () => void;
  onOpenFile?: () => void;
}) {
  const t = useTranslations("chat");
  const where = doc.type === "web" ? (doc.url ? hostOf(doc.url) : null) : (base ?? null);

  const body = (
    <>
      <p className="text-foreground truncate text-[13px] font-medium" title={doc.title}>
        {doc.title}
      </p>
      <p className="text-muted-foreground truncate text-xs">
        {[where, doc.passages > 1 ? t("passageCount", { count: doc.passages }) : null]
          .filter(Boolean)
          .join(" · ")}
      </p>
    </>
  );

  return (
    <li className="hover:bg-foreground/[0.03] flex items-center gap-2.5 px-3 py-2 transition-colors">
      <SourceMark type={doc.type} title={doc.title} url={doc.url} />
      <button type="button" onClick={onShow} className="min-w-0 flex-1 text-left">
        {body}
      </button>
      {onOpenFile && (
        <button
          type="button"
          onClick={onOpenFile}
          title={t("openOriginal")}
          aria-label={t("openOriginalNamed", { name: doc.title })}
          className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] rounded-md p-1.5"
        >
          <FileSearch className="h-4 w-4" />
        </button>
      )}
      {doc.type === "web" && doc.url && (
        <a
          href={doc.url}
          target="_blank"
          rel="noopener noreferrer"
          title={t("openPage")}
          aria-label={t("openPageNamed", { name: doc.title })}
          className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] rounded-md p-1.5"
        >
          <ExternalLink className="h-4 w-4" />
        </a>
      )}
    </li>
  );
}
