"use client";

import { useTranslations } from "next-intl";

import { DocumentCard } from "./document-card";
import type { ProducedDocument } from "@/lib/turn-documents";

/**
 * What the turn made, under what it said.
 *
 * A deck produced by a script is one line inside the fold above the reply - *Ran
 * office.py deck* - and a fold is closed by default, so the file the person asked
 * for arrived as a paragraph telling them a path on a machine they cannot see. This
 * is that file, where the answer is: the name, the two things to do with it, and its
 * first pages at a size a slide can be recognised at.
 *
 * Under the answer rather than inside the step, because "做一份 PPT" ends in a file
 * and not in a log: the work is worth folding away, the deliverable is not.
 *
 * Nothing is drawn for a turn that made no document - most of them - so this costs a
 * transcript one hook and no space.
 */
export function TurnDocuments({
  conversationId,
  documents,
}: {
  conversationId?: string;
  documents: readonly ProducedDocument[];
}) {
  const t = useTranslations("files.office");

  if (documents.length === 0) return null;

  return (
    <div className="mt-2 max-w-xl space-y-3">
      <p className="text-muted-foreground text-xs font-medium">{t("madeThisTurn")}</p>
      {documents.map((document) => (
        <DocumentCard
          key={document.name}
          conversationId={conversationId}
          path={document.name}
          preview
        />
      ))}
    </div>
  );
}
