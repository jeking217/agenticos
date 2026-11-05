"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ExternalLink, Globe2, KeyRound, Lock, Pencil } from "lucide-react";
import { useTranslations } from "next-intl";

import { PageHeader } from "@/components/dashboard/page-header";
import { Button } from "@/components/ui";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import {
  connectCmsToken,
  forgetCmsToken,
  getCmsStatus,
  listSites,
  type CmsStatus,
  type SiteCard,
} from "@/lib/cms-api";
import { ApiError, getErrorMessage } from "@/lib/api-error";
import { ROUTES } from "@/lib/constants";
import { useRouter } from "@/lib/locale-navigation";
import { cn } from "@/lib/utils";

/**
 * The one step that connects the two systems.
 *
 * Signing in here spends the platform's token on the identity it names and keeps
 * none, so there is nothing to forward: the person is signed in to both already,
 * and this asks for the CMS's own `token` header once. Kept as a form rather than
 * a silent redirect because the token is a password - it is pasted deliberately,
 * and it can be replaced when it expires.
 */
function ConnectForm({ onConnected }: { onConnected: () => void }) {
  const t = useTranslations("sites");
  const tErrors = useTranslations("errors");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!token.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await connectCmsToken(token.trim());
      setToken("");
      onConnected();
    } catch (cause) {
      setError(getErrorMessage(cause, tErrors));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="border-border bg-card max-w-xl rounded-xl border p-4">
      <div className="mb-1 flex items-center gap-2">
        <KeyRound className="h-4 w-4" />
        <h2 className="text-sm font-semibold">{t("connectTitle")}</h2>
      </div>
      <p className="text-muted-foreground mb-3 text-xs">{t("connectDescription")}</p>
      <input
        type="password"
        value={token}
        onChange={(event) => setToken(event.target.value)}
        placeholder={t("connectPlaceholder")}
        autoComplete="off"
        spellCheck={false}
        className="border-border bg-background focus:ring-ring w-full rounded-lg border px-3 py-2 font-mono text-xs focus:ring-2 focus:outline-none"
      />
      {error ? <p className="text-destructive mt-2 text-xs">{error}</p> : null}
      <div className="mt-3 flex items-center gap-2">
        <Button type="submit" size="sm" disabled={busy || !token.trim()}>
          {busy ? t("connecting") : t("connectAction")}
        </Button>
        <span className="text-muted-foreground text-[11px]">{t("connectHint")}</span>
      </div>
    </form>
  );
}

/**
 * The sites this person may open.
 *
 * A card per site, with its cover, its address and one door: edit. The door is
 * disabled - with the reason on it - when the CMS will refuse the write, which
 * is decided by the server (`editable`) rather than guessed here. A page of
 * buttons that fail on click is the thing this avoids.
 */
export default function SitesPage() {
  const t = useTranslations("sites");
  const tErrors = useTranslations("errors");
  const router = useRouter();
  const [status, setStatus] = useState<CmsStatus | null>(null);
  const [sites, setSites] = useState<SiteCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const answer = await getCmsStatus();
      setStatus(answer);
      // `has_token` first: asking for the site list without one is a guaranteed
      // 401 whose message ("session expired") describes the wrong problem.
      const ready = answer.can_use && answer.has_token;
      setSites(ready ? await listSites() : []);
    } catch (cause) {
      // A stored token the CMS no longer honours leaves this page with nothing
      // it can do: the app cannot renew a CMS session, and "session expired"
      // would describe a session the person never had here. Drop the token and
      // let the form ask again, which is the one action that fixes it.
      if (cause instanceof ApiError && cause.code === "cms_token_expired") {
        await forgetCmsToken().catch(() => undefined);
        setStatus((current) => (current ? { ...current, has_token: false } : current));
        setSites([]);
      } else {
        setError(getErrorMessage(cause, tErrors));
      }
    } finally {
      setLoading(false);
    }
  }, [tErrors]);

  useEffect(() => {
    void load();
  }, [load]);

  const header = <PageHeader title={t("title")} description={t("pageDescription")} />;

  if (loading) {
    return (
      <>
        {header}
        <LoadingState />
      </>
    );
  }

  if (error) {
    return (
      <>
        {header}
        <ErrorState
          title={t("loadFailed")}
          description={error}
          cta={{ label: t("retry"), onClick: () => void load() }}
        />
      </>
    );
  }

  if (status && !status.can_use) {
    return (
      <>
        {header}
        <EmptyState
          icon={Globe2}
          title={t("unavailableTitle")}
          description={status.reason ?? t("unavailableHint")}
        />
      </>
    );
  }

  if (status && status.can_use && !status.has_token) {
    return (
      <>
        {header}
        <ConnectForm onConnected={() => void load()} />
      </>
    );
  }

  if (sites.length === 0) {
    return (
      <>
        {header}
        <EmptyState
          icon={Globe2}
          title={t("emptyTitle")}
          description={t("emptyDescription")}
          cta={{ label: t("retry"), onClick: () => void load() }}
        />
      </>
    );
  }

  return (
    <>
      {header}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {sites.map((site) => (
          <article key={site.id} className="border-border bg-card flex flex-col overflow-hidden rounded-xl border">
            <div className="bg-muted relative aspect-[16/9] w-full overflow-hidden">
              {site.cover_url ? (
                // eslint-disable-next-line @next/next/no-img-element - a CMS cover, not a Next asset
                <img src={site.cover_url} alt="" className="h-full w-full object-cover" />
              ) : (
                <div className="text-muted-foreground flex h-full items-center justify-center">
                  <Globe2 className="h-8 w-8" />
                </div>
              )}
            </div>
            <div className="flex flex-1 flex-col gap-1 p-3">
              <h2 className="truncate text-sm font-semibold">{site.title}</h2>
              <p className="text-muted-foreground truncate font-mono text-xs">
                {site.domain ?? site.ctx}
              </p>
              {!site.editable && site.reason ? (
                <p className="text-muted-foreground flex items-start gap-1 text-[11px]">
                  <Lock className="mt-0.5 h-3 w-3 shrink-0" />
                  {site.reason}
                </p>
              ) : null}
            </div>
            <div className="border-border flex items-center gap-2 border-t px-3 py-2">
              <Button
                size="sm"
                variant="outline"
                disabled={!site.editable}
                onClick={() => router.push(ROUTES.SITE_DETAIL(site.id))}
                className={cn(!site.editable && "opacity-60")}
              >
                <Pencil className="h-3.5 w-3.5" />
                {t("openEditor")}
              </Button>
              {site.domain ? (
                <a
                  href={`https://${site.domain}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-muted-foreground hover:text-foreground ml-auto inline-flex items-center gap-1 text-xs"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  {t("openSite")}
                </a>
              ) : null}
            </div>
          </article>
        ))}
      </div>
    </>
  );
}
