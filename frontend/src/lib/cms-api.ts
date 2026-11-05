/**
 * The CMS site endpoints, as the console calls them.
 *
 * One module per feature is the convention here (`projects-api.ts`,
 * `suggestions-api.ts`), and the reason is the same every time: the shapes the
 * API returns are declared once, next to the functions that fetch them, so a
 * page never parses JSON by hand and a rename breaks the build rather than a
 * screen somebody opens a week later.
 *
 * Every call is a step of the flow - pull, diff, verify, push, release - and
 * nothing here combines two of them. The console shows the result of each
 * before offering the next, which is what makes "publish" a decision.
 */

import { apiClient } from "./api-client";

export interface CmsStatus {
  configured: boolean;
  platform_linked: boolean;
  can_use: boolean;
  /** The token kept at sign-in is still worth trying, so there is nothing to ask. */
  has_token: boolean;
  reason: string | null;
}

export interface SiteCard {
  id: number;
  title: string;
  ctx: string;
  domain: string | null;
  cover_url: string | null;
  space_id: number | null;
  space_vers: number | null;
  editable: boolean;
  reason: string | null;
}

export interface FileChange {
  path: string;
  /** `A`, `M` or `D` - the letter the tree shows beside the file. */
  status: string;
  size: number;
  resource_id: number | null;
}

export interface Diff {
  changes: FileChange[];
  skipped: { path: string; reason: string }[];
  counts: { added?: number; modified?: number; deleted?: number };
}

export interface PullResult {
  ok: boolean;
  files: number;
  skipped: { path: string; reason: string }[];
  baseline: string | null;
  created: boolean;
}

export interface VerifyReport {
  ok: boolean;
  failures: string[];
  detail: string | null;
  checked: number;
}

export interface PushReport {
  ok: boolean;
  saved: string[];
  upgraded: string[];
  deleted: string[];
  failed: { path: string; reason: string }[];
}

export interface DiscardReport {
  ok: boolean;
  /** The files that went back to what is live. */
  discarded: string[];
  /** Files whose CMS draft was taken back too, because they had been pushed. */
  recalled: string[];
  failed: { path: string; reason: string }[];
}

export interface Blocker {
  code: string;
  message: string;
}

export interface ReleasePlan {
  ok: boolean;
  siteId: number;
  files: string[];
  blockers: Blocker[];
  warnings: string[];
  foreignDrafts: string[];
  verifyFailures: string[];
}

export interface ReleaseResult {
  ok: boolean;
  released: number;
  files: string[];
  tag: string | null;
  warnings: string[];
  blockers: Blocker[];
}

export interface RollbackResult {
  ok: boolean;
  restored: string[];
  tag: string | null;
  released: number;
}

export interface SuggestionChip {
  action: string;
  label: string;
  detail: string;
  payload: Record<string, unknown>;
}

export interface WorkspaceInfo {
  files: string[];
  branch: string;
  releases: string[];
}

const base = (siteId: number) => `/cms/sites/${siteId}`;

/** Whether the module may be opened at all, and why not. */
export function getCmsStatus(): Promise<CmsStatus> {
  return apiClient.get<CmsStatus>("/cms/status");
}

/**
 * Forget the stored token.
 *
 * There is no `connect` beside it: the token is kept by the sign-in that obtained
 * it, so the console never posts one. This only ever removes - on sign-out, or
 * when the CMS has stopped accepting the token that is on file.
 */
export function forgetCmsToken(): Promise<void> {
  return apiClient.delete<void>("/cms/session");
}

/** The sites this person may open, with the edit door already decided. */
export function listSites(): Promise<SiteCard[]> {
  return apiClient.get<{ sites: SiteCard[] }>("/cms/sites").then((answer) => answer.sites);
}

/** Remember that this person opened this site. */
export function bindSite(site: SiteCard): Promise<void> {
  return apiClient.post<void>(`${base(site.id)}/bind`, {
    site_id: site.id,
    title: site.title,
    ctx: site.ctx,
    domain: site.domain,
    space_id: site.space_id,
    space_vers: site.space_vers,
  });
}

export function getWorkspace(siteId: number): Promise<WorkspaceInfo> {
  return apiClient.get<WorkspaceInfo>(`${base(siteId)}/workspace`);
}

/** Fetch the site's code and lay down the baseline. */
export function pullSite(siteId: number): Promise<PullResult> {
  return apiClient.post<PullResult>(`${base(siteId)}/pull`);
}

export function getDiff(siteId: number): Promise<Diff> {
  return apiClient.get<Diff>(`${base(siteId)}/diff`);
}

/**
 * One file's text, as the editor shows it.
 *
 * The path is encoded: a resource path starts with `/` and holds `/`, and an
 * unencoded one would be read as extra URL segments by everything in between.
 */
export function getSiteFile(siteId: number, path: string): Promise<{ path: string; text: string }> {
  return apiClient.get<{ path: string; text: string }>(
    `${base(siteId)}/file?path=${encodeURIComponent(path)}`,
  );
}

/**
 * Undo the edits to these files, putting them back to what is live.
 *
 * Not a rollback: everything else the person changed stays. A file that was
 * already pushed has its CMS draft taken back as well, which is why the answer
 * names both `discarded` and `recalled`.
 */
export function discardSiteFiles(siteId: number, paths: string[]): Promise<DiscardReport> {
  return apiClient.post<DiscardReport>(`${base(siteId)}/discard`, { paths });
}

/** Beetl-check the changed templates. Nothing is saved. */
export function verifySite(siteId: number): Promise<VerifyReport> {
  return apiClient.post<VerifyReport>(`${base(siteId)}/verify`);
}

/** Save the changed files as CMS drafts. */
export function pushSite(siteId: number): Promise<PushReport> {
  return apiClient.post<PushReport>(`${base(siteId)}/push`);
}

/** The confirmation sheet, without publishing anything. */
export function getReleasePlan(siteId: number): Promise<ReleasePlan> {
  return apiClient.get<ReleasePlan>(`${base(siteId)}/release/plan`);
}

/**
 * Publish.
 *
 * `confirmed` is not optional and not defaulted: the CMS publishes every
 * unpublished draft of the site, so the request carries the person's own click
 * and the drafts they were shown.
 */
export function releaseSite(siteId: number, foreignDrafts: string[] = []): Promise<ReleaseResult> {
  return apiClient.post<ReleaseResult>(`${base(siteId)}/release`, {
    confirmed: true,
    foreign_drafts: foreignDrafts,
  });
}

export function rollbackSite(siteId: number, to?: string): Promise<RollbackResult> {
  return apiClient.post<RollbackResult>(`${base(siteId)}/rollback`, { to: to ?? null });
}

/** The chips to offer now: fix, diff, preview or publish. */
export function getSuggestions(siteId: number): Promise<SuggestionChip[]> {
  return apiClient.get<SuggestionChip[]>(`${base(siteId)}/suggestions`);
}

export function getPreviewUrl(siteId: number, path = "/"): Promise<string> {
  return apiClient
    .get<{ url: string }>(`${base(siteId)}/preview-url?path=${encodeURIComponent(path)}`)
    .then((answer) => answer.url);
}
