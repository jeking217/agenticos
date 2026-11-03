"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { DIALOG_BODY, DIALOG_BROAD, DIALOG_COLUMN } from "@/lib/dialog-sizes";
import { useUpdateModel } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";
import type { Dataset, DatasetFieldInput, DatasetFieldType } from "@/types/datasets";

const TYPES: DatasetFieldType[] = ["text", "integer", "numeric", "date", "timestamp", "boolean"];

interface DraftField extends DatasetFieldInput {
  key: string;
}

function toDraft(dataset: Dataset): DraftField[] {
  return dataset.fields.map((field) => ({
    key: field.name,
    name: field.name,
    display_name: field.display_name,
    data_type: field.data_type,
    is_nullable: field.is_nullable,
    is_primary_key: field.is_primary_key,
    synonyms: field.synonyms,
  }));
}

interface FieldEditorDialogProps {
  dataset: Dataset;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * The model, editable.
 *
 * A field's name is frozen once rows exist - the rows are JSON documents keyed by
 * it - so the input is disabled rather than removed: hiding it would make the
 * column look absent from the model it belongs to. The primary key is frozen by
 * the server for a stronger reason, and the UI says which one applies.
 */
export function FieldEditorDialog({ dataset, open, onOpenChange }: FieldEditorDialogProps) {
  const t = useTranslations("datasets");
  const save = useUpdateModel(dataset.id);
  // Seeded once, at mount: the dialog is rendered only while it is open, and the
  // caller keys it by dataset, so a different dataset is a different mount rather
  // than a prop the draft has to chase through an effect.
  const [fields, setFields] = useState<DraftField[]>(() => toDraft(dataset));
  const locked = dataset.current_version_no > 0;

  const setPrimaryKey = (key: string) =>
    setFields((current) =>
      current.map((field) => ({ ...field, is_primary_key: field.key === key })),
    );

  const submit = async () => {
    await save.mutateAsync({
      fields: fields.map(({ key: _key, ...field }) => field),
      note: t("modelNote"),
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn(DIALOG_COLUMN, DIALOG_BROAD)}>
        <DialogHeader>
          <DialogTitle>{t("modelTitle")}</DialogTitle>
          <DialogDescription>{locked ? t("modelLockedHint") : t("modelHint")}</DialogDescription>
        </DialogHeader>

        <div className={cn(DIALOG_BODY, "space-y-2 py-1")}>
          {fields.map((field, index) => (
            <div
              key={field.key}
              className="rounded-card border-border grid items-center gap-2 border p-2 sm:grid-cols-[1fr_1fr_8rem_auto]"
            >
              <Input
                value={field.name}
                readOnly={locked}
                disabled={locked}
                aria-label={t("fieldName")}
                onChange={(event) =>
                  setFields((current) =>
                    current.map((entry, position) =>
                      position === index ? { ...entry, name: event.target.value } : entry,
                    ),
                  )
                }
              />
              <Input
                value={field.display_name ?? ""}
                aria-label={t("displayName")}
                onChange={(event) =>
                  setFields((current) =>
                    current.map((entry, position) =>
                      position === index ? { ...entry, display_name: event.target.value } : entry,
                    ),
                  )
                }
              />
              <Select
                value={field.data_type}
                onValueChange={(value) =>
                  setFields((current) =>
                    current.map((entry, position) =>
                      position === index
                        ? { ...entry, data_type: value as DatasetFieldType }
                        : entry,
                    ),
                  )
                }
              >
                <SelectTrigger aria-label={t("fieldTypeLabel")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TYPES.map((type) => (
                    <SelectItem key={type} value={type}>
                      {t(`fieldType.${type}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant={field.is_primary_key ? "secondary" : "outline"}
                  disabled={locked}
                  onClick={() => setPrimaryKey(field.key)}
                >
                  {field.is_primary_key ? t("key") : t("setKey")}
                </Button>
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label={t("removeField")}
                  disabled={locked || fields.length <= 1}
                  onClick={() =>
                    setFields((current) => current.filter((_, position) => position !== index))
                  }
                >
                  <Trash2 className="size-4" aria-hidden />
                </Button>
              </div>
            </div>
          ))}

          <Button
            type="button"
            variant="outline"
            onClick={() =>
              setFields((current) => [
                ...current,
                {
                  key: `new-${current.length}`,
                  name: `field_${current.length + 1}`,
                  display_name: t("newField"),
                  data_type: "text",
                  is_primary_key: false,
                  is_nullable: true,
                },
              ])
            }
          >
            <Plus className="size-4" aria-hidden />
            {t("addField")}
          </Button>

          <div className="text-muted-foreground flex flex-wrap items-center gap-2 pt-2 text-xs">
            <Badge variant="outline">{t("modelVersion", { version: dataset.model_version })}</Badge>
            <span>{t("keyFrozenHint")}</span>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button
            onClick={submit}
            loading={save.isPending}
            disabled={
              fields.some((field) => !field.name.trim()) ||
              !fields.some((field) => field.is_primary_key)
            }
          >
            {t("saveModel")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
