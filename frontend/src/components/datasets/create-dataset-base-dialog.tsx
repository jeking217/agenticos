"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from "@/components/ui";
import {
  NO_REASONING,
  ReasoningModelFields,
  reasoningIncomplete,
  reasoningPayload,
  useReasoningProviders,
  type ReasoningChoice,
} from "@/components/kb/reasoning-model-fields";
import { DIALOG_BODY, DIALOG_COLUMN, DIALOG_FORM } from "@/lib/dialog-sizes";
import { useCreateDatasetBase } from "@/hooks/use-datasets";
import { ROUTES } from "@/lib/constants";
import { cn } from "@/lib/utils";

/**
 * Creates the base that will hold datasets.
 *
 * A name, an audience, and the model that reads the workbooks put into it. No
 * embedding model: a dataset has no vector column and nothing to embed, so that
 * question belongs to the document form and nowhere else. The reading model is
 * optional - a base with none imports on the parser's rules - which is why the
 * field starts on "off" rather than demanding a provider.
 */
export function CreateDatasetBaseDialog() {
  const t = useTranslations("datasets");
  const router = useRouter();
  const create = useCreateDatasetBase();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState<"personal" | "org">("personal");
  const [reasoning, setReasoning] = useState<ReasoningChoice>(NO_REASONING);
  const { providers } = useReasoningProviders();
  const incomplete = reasoningIncomplete(
    reasoning,
    providers.find((entry) => entry.id === reasoning.provider),
  );

  const submit = async () => {
    // The audience is a decision, not a default: it was collected and then not
    // sent, so every base came back personal whatever was chosen.
    const base = await create.mutateAsync({
      name: name.trim(),
      description: description.trim() || null,
      scope,
      ...reasoningPayload(reasoning),
    });
    setOpen(false);
    setName("");
    setDescription("");
    setReasoning(NO_REASONING);
    router.push(ROUTES.DATASET_BASE(base.id));
  };

  return (
    <>
      <Button onClick={() => setOpen(true)} data-tour="dataset-new">
        <Plus className="size-4" aria-hidden />
        {t("newBase")}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className={cn(DIALOG_COLUMN, DIALOG_FORM)}>
          <DialogHeader>
            <DialogTitle>{t("newBaseTitle")}</DialogTitle>
            <DialogDescription>{t("newBaseDescription")}</DialogDescription>
          </DialogHeader>

          <div className={cn(DIALOG_BODY, "space-y-4 py-1")}>
            <FormField label={t("name")} htmlFor="dataset-base-name" required>
              <Input
                id="dataset-base-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("namePlaceholder")}
                maxLength={128}
              />
            </FormField>

            <FormField
              label={t("description")}
              htmlFor="dataset-base-description"
              description={t("descriptionHint")}
            >
              <Textarea
                id="dataset-base-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={500}
                rows={3}
              />
            </FormField>

            <FormField label={t("scope")} htmlFor="dataset-base-scope" description={t("scopeHint")}>
              <Select
                value={scope}
                onValueChange={(value) => setScope(value as "personal" | "org")}
              >
                <SelectTrigger id="dataset-base-scope">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="personal">{t("scopePersonal")}</SelectItem>
                  <SelectItem value="org">{t("scopeOrg")}</SelectItem>
                </SelectContent>
              </Select>
            </FormField>

            <ReasoningModelFields
              value={reasoning}
              onChange={setReasoning}
              idPrefix="dataset-base"
            />
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              {t("cancel")}
            </Button>
            <Button
              onClick={submit}
              loading={create.isPending}
              disabled={name.trim().length === 0 || incomplete}
            >
              {t("create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
