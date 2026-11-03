"use client";

import { useRef, useState } from "react";
import { Link2, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  Badge,
  Button,
  DataTable,
  FormField,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  type Column,
} from "@/components/ui";
import { useRelationCandidates, useRelationMutations, useRelations } from "@/hooks/use-datasets";
import type { Dataset, DatasetRelation, RelationCandidate } from "@/types/datasets";

import { RelationCanvas } from "./relation-canvas";

interface RelationPanelProps {
  dataset: Dataset;
  siblings: Dataset[];
}

/**
 * Registered joins, the ones the data suggests, and the canvas they live on.
 *
 * A suggestion is drawn as a suggestion: it carries a confidence and does not
 * become a join until somebody confirms it, because the compiler will allow it
 * from that moment and a wrong join silently multiplies rows.
 *
 * Drawing a line between two datasets does not save anything: it fills the form
 * in with that pair and shows what the suggestion engine made of it, so the
 * decision - fields, name, cardinality - is still made with the validation
 * result in front of the person (#T43).
 */
export function RelationPanel({ dataset, siblings }: RelationPanelProps) {
  const t = useTranslations("datasets");
  const { relations, isLoading } = useRelations(dataset.id);
  const { candidates } = useRelationCandidates(dataset.id, siblings.length > 0);
  const { create, remove } = useRelationMutations(dataset.id);

  const [target, setTarget] = useState("");
  const [local, setLocal] = useState("");
  const [foreign, setForeign] = useState("");
  const [name, setName] = useState("");
  const formRef = useRef<HTMLElement>(null);

  const targetDataset = siblings.find((entry) => entry.id === target);
  // What the suggestion engine thinks of the pair the form is on, shown before
  // saving rather than after a bad join has multiplied somebody's rows.
  const checked = candidates.find(
    (candidate) => candidate.target_dataset_id === target && !candidate.already_exists,
  );

  /** A line was drawn from another dataset to this one: fill the form in with it. */
  const connect = (otherId: string) => {
    const entry = siblings.find((sibling) => sibling.id === otherId);
    if (!entry) return;
    setTarget(entry.id);
    setName(`to_${entry.name}`.slice(0, 64));
    const suggestion = candidates.find(
      (candidate) => candidate.target_dataset_id === entry.id && !candidate.already_exists,
    );
    setLocal(suggestion?.local_fields[0] ?? "");
    setForeign(suggestion?.foreign_fields[0] ?? "");
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const columns: Column<DatasetRelation>[] = [
    {
      key: "name",
      header: t("relationName"),
      cell: (relation) => (
        <span className="flex items-center gap-2">
          <Link2 className="text-muted-foreground size-3.5" aria-hidden />
          <span className="font-medium">{relation.name}</span>
        </span>
      ),
    },
    {
      key: "target",
      header: t("relationTarget"),
      cell: (relation) => relation.target_dataset_name ?? relation.target_dataset_id,
    },
    {
      key: "on",
      header: t("relationOn"),
      cell: (relation) => (
        <span className="text-muted-foreground font-mono text-xs">
          {relation.local_fields.join(",")} → {relation.foreign_fields.join(",")}
        </span>
      ),
      hideBelow: "md",
    },
    {
      key: "cardinality",
      header: t("cardinality"),
      cell: (relation) => <Badge variant="outline">{relation.cardinality}</Badge>,
      className: "w-24",
    },
    {
      key: "status",
      header: t("status"),
      cell: (relation) => (
        <Badge variant={relation.status === "confirmed" ? "secondary" : "outline"}>
          {t(`relationStatus.${relation.status}`)}
        </Badge>
      ),
      className: "w-28",
    },
    {
      key: "actions",
      header: "",
      className: "w-12",
      cell: (relation) => (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("removeRelation")}
          onClick={() => remove.mutate(relation.id)}
        >
          <Trash2 className="size-4" aria-hidden />
        </Button>
      ),
    },
  ];

  const confirmCandidate = (candidate: RelationCandidate) =>
    create.mutate({
      target_dataset_id: candidate.target_dataset_id,
      name: candidate.name,
      cardinality: candidate.cardinality,
      local_fields: candidate.local_fields,
      foreign_fields: candidate.foreign_fields,
      status: "confirmed",
    });

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t("canvasTitle")}</h3>
        <p className="text-muted-foreground text-xs">{t("canvasHint")}</p>
        <RelationCanvas
          dataset={dataset}
          siblings={siblings}
          relations={relations}
          candidates={candidates}
          onConnect={connect}
        />
      </section>

      <DataTable
        columns={columns}
        rows={relations}
        loading={isLoading}
        getRowKey={(relation) => relation.id}
        empty={<p className="text-muted-foreground py-8 text-center text-sm">{t("noRelations")}</p>}
      />

      {candidates.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-sm font-medium">{t("candidates")}</h3>
          <p className="text-muted-foreground text-xs">{t("candidatesHint")}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {candidates
              .filter((candidate) => !candidate.already_exists)
              .map((candidate) => (
                <div
                  key={`${candidate.name}-${candidate.target_dataset_name}`}
                  className="rounded-card border-border flex items-center gap-3 border border-dashed p-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm">
                      {candidate.local_fields.join(",")} → {candidate.target_dataset_name}.
                      {candidate.foreign_fields.join(",")}
                    </p>
                    <p className="text-muted-foreground text-xs">
                      {t("confidence", { value: Math.round(candidate.confidence * 100) })}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    loading={create.isPending}
                    onClick={() => confirmCandidate(candidate)}
                  >
                    {t("confirm")}
                  </Button>
                </div>
              ))}
          </div>
        </section>
      )}

      <section ref={formRef} className="rounded-card border-border space-y-3 border p-4">
        <h3 className="text-sm font-medium">{t("addRelation")}</h3>
        {target && (
          <p
            data-testid="relation-check"
            className={
              checked
                ? "text-muted-foreground text-xs"
                : "text-xs text-amber-700 dark:text-amber-400"
            }
          >
            {checked
              ? t("checkPassed", {
                  fields: checked.local_fields.join(","),
                  target: checked.foreign_fields.join(","),
                  value: Math.round(checked.confidence * 100),
                })
              : t("checkUnverified", { name: targetDataset?.name ?? "" })}
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <FormField label={t("relationTarget")} htmlFor="relation-target">
            <Select
              value={target}
              onValueChange={(value) => {
                setTarget(value);
                const entry = siblings.find((sibling) => sibling.id === value);
                setName(entry ? `to_${entry.name}`.slice(0, 64) : "");
              }}
            >
              <SelectTrigger id="relation-target">
                <SelectValue placeholder={t("chooseDataset")} />
              </SelectTrigger>
              <SelectContent>
                {siblings.map((sibling) => (
                  <SelectItem key={sibling.id} value={sibling.id}>
                    {sibling.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormField>

          <FormField label={t("relationName")} htmlFor="relation-name">
            <Input
              id="relation-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={64}
            />
          </FormField>

          <FormField label={t("localField")} htmlFor="relation-local">
            <Select value={local} onValueChange={setLocal}>
              <SelectTrigger id="relation-local">
                <SelectValue placeholder={t("chooseField")} />
              </SelectTrigger>
              <SelectContent>
                {dataset.fields.map((field) => (
                  <SelectItem key={field.name} value={field.name}>
                    {field.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormField>

          <FormField label={t("foreignField")} htmlFor="relation-foreign">
            <Select value={foreign} onValueChange={setForeign}>
              <SelectTrigger id="relation-foreign">
                <SelectValue placeholder={t("chooseField")} />
              </SelectTrigger>
              <SelectContent>
                {(targetDataset?.fields ?? []).map((field) => (
                  <SelectItem key={field.name} value={field.name}>
                    {field.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormField>
        </div>
        <Button
          onClick={() =>
            create.mutate({
              target_dataset_id: target,
              name: name.trim(),
              cardinality: "n:1",
              local_fields: [local],
              foreign_fields: [foreign],
              status: "confirmed",
            })
          }
          loading={create.isPending}
          disabled={!target || !local || !foreign || name.trim().length === 0}
        >
          <Plus className="size-4" aria-hidden />
          {t("addRelation")}
        </Button>
      </section>
    </div>
  );
}
