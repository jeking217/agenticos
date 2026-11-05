"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { PageHeader } from "@/components/dashboard/page-header";
import { CmsWorkbench } from "@/components/cms/workbench";
import { ErrorState, LoadingState } from "@/components/states";
import { listSites, type SiteCard } from "@/lib/cms-api";
import { getErrorMessage } from "@/lib/api-error";
import { ROUTES } from "@/lib/constants";

/**
 * One site's workbench.
 *
 * The site's title comes from the same list the picker used, rather than a
 * dedicated endpoint: the list is already cached by the browser, the title is
 * the only thing this page needs from it, and a second endpoint for one string
 * is a second thing to keep in step.
 */
export default function SiteDetailPage({ params }: { params: { siteId: string } }) {
  const t = useTranslations("sites");
  const tErrors = useTranslations("errors");
  const [site, setSite] = useState<SiteCard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const sites = await listSites();
        if (cancelled) return;
        const found = sites.find((candidate) => String(candidate.id) === params.siteId) ?? null;
        setSite(found);
        setError(found ? null : t("siteGone"));
      } catch (cause) {
        if (!cancelled) setError(getErrorMessage(cause, tErrors));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params.siteId, t, tErrors]);

  if (loading) {
    return (
      <>
        <PageHeader title={t("editorTitle")} />
        <LoadingState />
      </>
    );
  }

  if (!site) {
    return (
      <>
        <PageHeader title={t("editorTitle")} breadcrumbs={[{ label: t("title"), href: ROUTES.SITES }]} />
        <ErrorState title={t("siteGone")} description={error ?? undefined} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={site.title}
        description={site.domain ?? site.ctx}
        breadcrumbs={[{ label: t("title"), href: ROUTES.SITES }, { label: site.title }]}
      />
      <div className="min-h-0 flex-1">
        <CmsWorkbench siteId={site.id} siteTitle={site.title} siteAddress={site.domain ?? site.ctx} />
      </div>
    </>
  );
}
