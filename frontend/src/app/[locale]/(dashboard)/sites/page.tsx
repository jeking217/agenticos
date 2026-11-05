"use client";

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Globe2, KeyRound, Lock, Pencil } from "lucide-react";
import { useTranslations } from "next-intl";

import { PageHeader } from "@/components/dashboard/page-header";
import { Button } from "@/components/ui";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { useAuth } from "@/hooks";
import {
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
 * The apps this person may open.
 *
 * A card per site, with its cover, its address and one door: edit. The door is
 * disabled - with the reason on it - when the CMS will refuse the write, which
 * is decided by the server (`editable`) rather than guessed here. A page of
 * buttons that fail on click is the thing this avoids.
 *
 * A session with no platform token cannot speak to the CMS at all, and there is
 * nothing this page can do about it: the token is kept by the sign-in that
 * obtained it, so the one repair is signing in again - which is what the page
 * offers, instead of a box to paste a token into.
 */
export default function SitesPage() {
  const t = useTranslations("sites");
  const tErrors = useTranslations("errors");
  const router = useRouter();
  const { logout } = useAuth();
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
      // A token the CMS no longer honours leaves this page with nothing it can
      // do: the app cannot renew a CMS session, and "session expired" would
      // describe a session the person never had here. Drop the token so the page
      // asks for a sign-in - the one action that mints a fresh one.
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
        <EmptyState
          icon={KeyRound}
          title={t("signInAgainTitle")}
          description={t("signInAgainDescription")}
          cta={{ label: t("signInAgain"), onClick: () => void logout() }}
        />
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
          <article
            key={site.id}
            className="border-border bg-card flex flex-col overflow-hidden rounded-xl border"
          >
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
