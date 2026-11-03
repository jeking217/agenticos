"use client";

import { useQuery } from "@tanstack/react-query";

import { InlineSecret } from "@/components/vault/inline-secret";
import { ProviderRow } from "@/components/vault/provider-row";
import {
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { useSecrets } from "@/hooks";
import { apiClient } from "@/lib/api-client";
import { useTranslations } from "next-intl";

/**
 * The model a dataset base reads its workbooks with.
 *
 * The same three questions the document form asks about its embedding model -
 * which provider, whose credential, which model - with one difference the job
 * forces: the model is **typed**, not chosen from a list. What reads a
 * spreadsheet is a chat model, and providers ship those weekly; a dropdown would
 * be a list of the ones that existed when this build was made, which is the
 * wrong list for the person who just pulled a model.
 *
 * Optional throughout. A base with no model imports on the parser's rules, which
 * is why the first option is "off" rather than a provider somebody has to pick
 * before the form will save.
 */

/** Where the provider choices come from - the deployment's own catalog. */
export interface ReasoningProvider {
  id: string;
  name: string;
  /** Runs with no credential at all: a server on this network, named by address. */
  keyless: boolean;
  /** Accepts an address of its own - a gateway, a proxy, a regional endpoint. */
  supports_base_url: boolean;
}

export interface ReasoningChoice {
  provider: string | null;
  model: string;
  secretId: string | null;
  baseUrl: string;
}

/** What the form starts on: no model, and therefore no key to choose. */
export const NO_REASONING: ReasoningChoice = {
  provider: null,
  model: "",
  secretId: null,
  baseUrl: "",
};

/**
 * The providers a base may read through.
 *
 * Its own route rather than `/providers/catalog`: that one is gated on seeing
 * agents, and somebody who may import a spreadsheet being unable to name the
 * model that reads it would be a permission answering a different question.
 */
export function useReasoningProviders() {
  const { data, isError } = useQuery({
    queryKey: ["kb", "reasoning-providers"],
    queryFn: () =>
      apiClient.get<{ items: ReasoningProvider[] }>("/kb/datasets/reasoning-providers"),
    // A build property, so it cannot go stale while a dialog is open.
    staleTime: Infinity,
  });
  return { providers: data?.items ?? [], unreadable: isError };
}

/** The value the select uses for "no model": Radix refuses an empty string. */
const OFF = "none";

/**
 * What is still missing before this model can be saved, or nothing.
 *
 * The same four rules the API applies, said early enough to be useful: a
 * provider with no model id, a keyed provider with no key, a self-hosted one
 * with no address. Checked here rather than by submitting and reading the
 * refusal, because every one of them is visible in the form that caused it.
 */
export function reasoningIncomplete(
  choice: ReasoningChoice,
  provider: ReasoningProvider | undefined,
): boolean {
  if (choice.provider === null) return false;
  if (choice.model.trim() === "") return true;
  if (provider === undefined) return true;
  if (provider.keyless) return choice.baseUrl.trim() === "";
  return choice.secretId === null || choice.secretId === "";
}

/** The four fields the base is created with, all null when it has no model. */
export function reasoningPayload(choice: ReasoningChoice) {
  if (choice.provider === null) {
    return {
      reasoning_provider: null,
      reasoning_model: null,
      reasoning_secret_id: null,
      reasoning_base_url: null,
    };
  }
  return {
    reasoning_provider: choice.provider,
    reasoning_model: choice.model.trim(),
    reasoning_secret_id: choice.secretId || null,
    reasoning_base_url: choice.baseUrl.trim() || null,
  };
}

export function ReasoningModelFields({
  value,
  onChange,
  idPrefix,
  error,
}: {
  value: ReasoningChoice;
  onChange: (next: ReasoningChoice) => void;
  /** So two of these on one screen do not share input ids. */
  idPrefix: string;
  error?: string;
}) {
  const t = useTranslations("datasets");
  const { providers, unreadable } = useReasoningProviders();
  const { secrets } = useSecrets();

  const chosen = providers.find((entry) => entry.id === value.provider) ?? null;
  // A key's purpose *is* the provider it answers for, which is what makes this a
  // lookup rather than a convention - and what stops an OpenRouter key being
  // offered for OpenAI's address.
  const keys = secrets.filter((secret) => secret.purpose === value.provider);

  const providerValue = value.provider ?? OFF;

  return (
    <section className="space-y-3" data-tour="kb-dialog-reasoning">
      <div className="space-y-1">
        <h3 className="text-foreground text-sm font-semibold">{t("reasoningTitle")}</h3>
        <p className="text-muted-foreground text-xs">{t("reasoningHint")}</p>
      </div>

      {unreadable ? (
        <p className="text-destructive text-sm">{t("reasoningUnreadable")}</p>
      ) : (
        <>
          <div className="space-y-1.5">
            <Label htmlFor={`${idPrefix}-reasoning-provider`}>{t("reasoningProvider")}</Label>
            <Select
              value={providerValue}
              onValueChange={(next) =>
                onChange({
                  provider: next === OFF ? null : next,
                  // A key or an address chosen for the provider being left
                  // behind would be sent to the new one, which is the failure
                  // this whole field exists to prevent.
                  secretId: null,
                  baseUrl: "",
                  model: "",
                })
              }
            >
              <SelectTrigger id={`${idPrefix}-reasoning-provider`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={OFF}>{t("reasoningOff")}</SelectItem>
                {providers.map((entry) => (
                  <SelectItem key={entry.id} value={entry.id} textValue={entry.name}>
                    <ProviderRow provider={entry.id} name={entry.name} />
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {chosen !== null && (
            <>
              {chosen.keyless ? (
                <div className="space-y-1.5">
                  <Label htmlFor={`${idPrefix}-reasoning-server`}>{t("reasoningServer")}</Label>
                  <Input
                    id={`${idPrefix}-reasoning-server`}
                    type="url"
                    value={value.baseUrl}
                    onChange={(event) => onChange({ ...value, baseUrl: event.target.value })}
                    placeholder="http://10.0.0.9:11434/v1"
                  />
                  <p className="text-muted-foreground text-xs">
                    {t("reasoningServerHint", { provider: chosen.name })}
                  </p>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor={`${idPrefix}-reasoning-key`}>{t("reasoningKey")}</Label>
                  <Select
                    value={value.secretId ?? ""}
                    onValueChange={(next) => onChange({ ...value, secretId: next })}
                  >
                    <SelectTrigger id={`${idPrefix}-reasoning-key`}>
                      <SelectValue placeholder={t("reasoningChooseKey")} />
                    </SelectTrigger>
                    <SelectContent>
                      {keys.map((secret) => (
                        <SelectItem key={secret.id} value={secret.id} textValue={secret.name}>
                          <ProviderRow
                            provider={value.provider ?? ""}
                            name={secret.name}
                            hint={secret.hint}
                          />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <InlineSecret
                    kind="api_key"
                    purpose={value.provider ?? "custom"}
                    suggestedName={t("reasoningKeyName", { provider: chosen.name })}
                    onCreated={(secretId) => onChange({ ...value, secretId })}
                  />
                </div>
              )}

              {chosen.supports_base_url && !chosen.keyless && (
                <div className="space-y-1.5">
                  <Label htmlFor={`${idPrefix}-reasoning-url`}>{t("reasoningBaseUrl")}</Label>
                  <Input
                    id={`${idPrefix}-reasoning-url`}
                    type="url"
                    value={value.baseUrl}
                    onChange={(event) => onChange({ ...value, baseUrl: event.target.value })}
                    placeholder={t("reasoningBaseUrlPlaceholder")}
                  />
                  <p className="text-muted-foreground text-xs">{t("reasoningBaseUrlHint")}</p>
                </div>
              )}

              <div className="space-y-1.5">
                <Label htmlFor={`${idPrefix}-reasoning-model`}>{t("reasoningModel")}</Label>
                <Input
                  id={`${idPrefix}-reasoning-model`}
                  value={value.model}
                  onChange={(event) => onChange({ ...value, model: event.target.value })}
                  placeholder={t("reasoningModelPlaceholder")}
                  maxLength={128}
                />
                <p className="text-muted-foreground text-xs">{t("reasoningModelHint")}</p>
              </div>
            </>
          )}
        </>
      )}

      {error !== undefined && <p className="text-destructive text-xs">{error}</p>}
    </section>
  );
}
