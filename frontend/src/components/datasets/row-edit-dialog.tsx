"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Input,
  Textarea,
} from "@/components/ui";
import { DIALOG_BODY, DIALOG_COLUMN, DIALOG_FORM } from "@/lib/dialog-sizes";
import { useRowMutations } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";
import type { Dataset, DatasetField, DatasetRow } from "@/types/datasets";

export type RowAction = "edit" | "delete";

interface RowEditDialogProps {
  dataset: Dataset;
  row: DatasetRow | null;
  action: RowAction;
  onOpenChange: (open: boolean) => void;
}

function initial(field: DatasetField, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return String(value);
  return String(value);
}

/**
 * One row, one reason, one new version.
 *
 * The reason is required rather than optional: a maintenance version is read
 * months later by somebody asking why a number moved, and "edited" answers that
 * question no better than a blank. The primary key is shown and not editable -
 * changing it would be a different record, so the correct action is a delete and
 * an add, which leaves both histories legible.
 */
export function RowEditDialog({ dataset, row, action, onOpenChange }: RowEditDialogProps) {
  const t = useTranslations("datasets");
  const { update, remove } = useRowMutations(dataset.id);
  // Seeded once, at mount: the caller renders this only while a row is chosen and
  // keys it by record, so a different row is a different mount - which is why the
  // values never have to be re-derived from a prop inside an effect.
  const [values, setValues] = useState<Record<string, string | boolean>>(() =>
    row
      ? Object.fromEntries(
          dataset.fields.map((field) => [field.name, initial(field, row.values[field.name])]),
        )
      : {},
  );
  const [reason, setReason] = useState("");

  const editable = dataset.fields.filter(
    (field) => !dataset.primary_key_fields.includes(field.name),
  );

  const submit = async () => {
    if (!row) return;
    if (action === "delete") {
      await remove.mutateAsync({ recordKey: row.record_key, reason: reason.trim() });
    } else {
      const payload: Record<string, unknown> = {};
      for (const field of editable) {
        const next = values[field.name];
        const previous = initial(field, row.values[field.name]);
        const asText = typeof next === "boolean" ? String(next) : (next ?? "");
        if (asText !== previous) {
          payload[field.name] =
            field.data_type === "boolean"
              ? next === true
              : field.data_type === "integer"
                ? Number(asText)
                : field.data_type === "numeric"
                  ? Number(asText)
                  : asText;
        }
      }
      if (Object.keys(payload).length === 0) {
        onOpenChange(false);
        return;
      }
      await update.mutateAsync({
        recordKey: row.record_key,
        values: payload,
        reason: reason.trim(),
      });
    }
    onOpenChange(false);
  };

  const pending = update.isPending || remove.isPending;

  return (
    <Dialog open={row !== null} onOpenChange={onOpenChange}>
      <DialogContent className={cn(DIALOG_COLUMN, DIALOG_FORM)}>
        <DialogHeader>
          <DialogTitle>{action === "delete" ? t("deleteRowTitle") : t("editRowTitle")}</DialogTitle>
          <DialogDescription>
            {action === "delete" ? t("deleteRowDescription") : t("editRowDescription")}
          </DialogDescription>
        </DialogHeader>

        <div className={cn(DIALOG_BODY, "space-y-4 py-1")}>
          <div className="rounded-card border-border bg-muted/40 border p-3 text-sm">
            <p className="font-medium">{t("recordKey")}</p>
            <p className="text-muted-foreground font-mono text-xs break-all">{row?.record_key}</p>
          </div>

          {action === "edit" && (
            <div className="space-y-3">
              {editable.map((field) => (
                <FormField
                  key={field.name}
                  label={field.display_name}
                  htmlFor={`row-${field.name}`}
                  description={field.is_primary_key ? t("keyNotEditable") : undefined}
                >
                  {field.data_type === "boolean" ? (
                    <div className="flex h-9 items-center">
                      <Checkbox
                        id={`row-${field.name}`}
                        checked={values[field.name] === true || values[field.name] === "true"}
                        onCheckedChange={(checked) =>
                          setValues((current) => ({ ...current, [field.name]: checked === true }))
                        }
                      />
                    </div>
                  ) : (
                    <Input
                      id={`row-${field.name}`}
                      value={String(values[field.name] ?? "")}
                      onChange={(event) =>
                        setValues((current) => ({ ...current, [field.name]: event.target.value }))
                      }
                      inputMode={
                        field.data_type === "numeric" || field.data_type === "integer"
                          ? "decimal"
                          : undefined
                      }
                    />
                  )}
                </FormField>
              ))}
            </div>
          )}

          <FormField
            label={t("reason")}
            htmlFor="row-reason"
            required
            description={t("reasonHint")}
          >
            <Textarea
              id="row-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={3}
              maxLength={500}
            />
          </FormField>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button
            variant={action === "delete" ? "destructive" : "default"}
            onClick={submit}
            loading={pending}
            disabled={reason.trim().length < 3}
          >
            {action === "delete" ? t("confirmDelete") : t("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
