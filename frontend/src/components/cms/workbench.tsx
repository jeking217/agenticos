"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowDownToLine,
  CheckCircle2,
  Eye,
  FileCode2,
  GitCompare,
  Monitor,
  Rocket,
  Save,
  ShieldCheck,
  Smartphone,
  Undo2,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { Button, LineDiff } from "@/components/ui";
import { ErrorState, LoadingState } from "@/components/states";
import { CodeEditor } from "@/components/cms/code-editor";
import {
  discardSiteFiles,
  getDiff,
  getPreviewUrl,
  getReleasePlan,
  getSiteFile,
  getWorkspace,
  pullSite,
  pushSite,
  releaseSite,
  verifySite,
  type Diff,
  type ReleasePlan,
  type VerifyReport,
} from "@/lib/cms-api";
import {
  buildTree,
  changedFiles,
  countsOf,
  dirtyFiles,
  editFile,
  initialWorkbench,
  isDirty,
  languageOf,
  openFile,
  recoveryFor,
  removeFile,
  resetFile,
  type WorkbenchState,
} from "@/lib/cms-workspace";
import { previewFrame, type PreviewDevice } from "@/lib/cms-preview";
import { getErrorMessage } from "@/lib/api-error";
import { cn } from "@/lib/utils";

/**
 * The editor: a tree, the file, the live preview, and one bar of actions.
 *
 * The order on the bar is the order of the flow - fetch, save as draft, check,
 * publish - and each button is disabled until the step before it has happened.
 * That is not decoration: a publish that skipped verification would put a
 * template that does not parse in front of visitors, and the CMS would accept it.
 *
 * The preview is an `iframe` pointing at this deployment's proxy, never at the
 * CMS. It is sandboxed without `allow-same-origin`, so a page from the CMS
 * cannot reach into the console's session - it renders, and that is all.
 *
 * Opening a file reads it from the workspace, not from the CMS: what the person
 * edits is what a push would send, and a pull may have moved the file on since
 * the CMS last saw it. That is also why a discard re-reads: the file changed
 * underneath the pane.
 */

interface WorkbenchProps {
  siteId: number;
  siteTitle: string;
  siteAddress: string | null;
}

type Phase = "loading" | "ready" | "error";

export function CmsWorkbench({ siteId, siteTitle, siteAddress }: WorkbenchProps) {
  const t = useTranslations("sites");
  const tErrors = useTranslations("errors");
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<WorkbenchState>(initialWorkbench);
  const [branch, setBranch] = useState("main");
  const [diff, setDiff] = useState<Diff | null>(null);
  const [verify, setVerify] = useState<VerifyReport | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewNonce, setPreviewNonce] = useState(0);
  const [device, setDevice] = useState<PreviewDevice>("desktop");
  const [plan, setPlan] = useState<ReleasePlan | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setPhase("loading");
    try {
      const [workspace, changes, url] = await Promise.all([
        getWorkspace(siteId),
        getDiff(siteId),
        getPreviewUrl(siteId, "/").catch(() => null),
      ]);
      setState((current) => ({ ...current, files: workspace.files, changes: changes.changes }));
      setBranch(workspace.branch);
      setDiff(changes);
      setPreviewUrl(url);
      setPhase("ready");
    } catch (cause) {
      setError(getErrorMessage(cause, tErrors));
      setPhase("error");
    }
  }, [siteId, tErrors]);

  useEffect(() => {
    void load();
  }, [load]);

  const tree = useMemo(() => buildTree(state.files, state.changes), [state.files, state.changes]);
  const openPath = state.openPath;
  const openText = openPath ? state.drafts[openPath] ?? "" : "";
  const savedText = openPath ? state.saved[openPath] ?? "" : "";
  const dirty = dirtyFiles(state);
  const counts = countsOf(state.changes);
  const frame = previewFrame(previewUrl, { device, nonce: previewNonce });

  async function run(name: string, work: () => Promise<void>) {
    setBusy(name);
    setNotice(null);
    setError(null);
    try {
      await work();
    } catch (cause) {
      setError(getErrorMessage(cause, tErrors));
    } finally {
      setBusy(null);
    }
  }

  const open = (path: string) =>
    run(`open:${path}`, async () => {
      if (state.openPath === path) return;
      const file = await getSiteFile(siteId, path);
      // A draft already in memory survives: switching files must not throw away
      // an edit the person has not saved yet.
      setState((current) => openFile(current, path, file.text));
    });

  const pull = () =>
    run("pull", async () => {
      const result = await pullSite(siteId);
      setNotice(t("pulled", { count: result.files }));
      await load();
      setPreviewNonce((nonce) => nonce + 1);
    });

  const save = () =>
    run("push", async () => {
      const report = await pushSite(siteId);
      setNotice(
        report.ok
          ? t("pushed", { count: report.saved.length })
          : t("pushPartly", { failed: report.failed.length }),
      );
      setDiff(await getDiff(siteId));
      // The drafts are what the preview renders, so it has to fetch again.
      setPreviewNonce((nonce) => nonce + 1);
    });

  const check = () =>
    run("verify", async () => {
      const report = await verifySite(siteId);
      setVerify(report);
      setNotice(report.ok ? t("verified", { count: report.checked }) : t("verifyFailed"));
    });

  const discard = (path: string) =>
    run(`discard:${path}`, async () => {
      const report = await discardSiteFiles(siteId, [path]);
      setNotice(t("discarded", { count: report.discarded.length }));
      setDiff(await getDiff(siteId));
      setPreviewNonce((nonce) => nonce + 1);
      // The file moved underneath the pane. A discarded addition has no file
      // left to read, which is not a failure - it is the point.
      const text = await getSiteFile(siteId, path)
        .then((file) => file.text)
        .catch(() => null);
      setState((current) =>
        text === null ? removeFile(current, path) : resetFile(current, path, text),
      );
    });

  const publish = () =>
    run("release", async () => {
      const sheet = await getReleasePlan(siteId);
      setPlan(sheet);
    });

  const confirmRelease = () =>
    run("release", async () => {
      const result = await releaseSite(siteId, plan?.foreignDrafts ?? []);
      setPlan(null);
      setVerify(null);
      setNotice(result.ok ? t("released", { count: result.released }) : t("releaseBlocked"));
      await load();
      setPreviewNonce((nonce) => nonce + 1);
    });

  if (phase === "loading") return <LoadingState />;
  if (phase === "error") {
    return (
      <ErrorState
        title={t("loadFailed")}
        description={error ?? undefined}
        cta={{ label: t("retry"), onClick: () => void load() }}
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[240px_minmax(0,1fr)_minmax(0,420px)]">
        <aside className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-xl border">
          <div className="border-border flex items-center justify-between border-b px-3 py-2">
            <span className="text-muted-foreground text-xs font-medium">{t("files")}</span>
            <span className="text-muted-foreground text-xs">{state.files.length}</span>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {tree.length === 0 ? (
              <p className="text-muted-foreground px-2 py-6 text-xs">{t("noFiles")}</p>
            ) : (
              <FileTree nodes={tree} openPath={openPath} onOpen={open} dirty={dirty} />
            )}
          </div>
        </aside>

        <section className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-xl border">
          <header className="border-border flex items-center justify-between gap-2 border-b px-3 py-2">
            <span className="truncate font-mono text-xs">{openPath ?? t("pickFile")}</span>
            <span className="text-muted-foreground text-xs">
              {openPath ? `${languageOf(openPath)}${isDirty(state, openPath) ? " · *" : ""}` : ""}
            </span>
          </header>
          <CodeEditor
            path={openPath}
            value={openText}
            onChange={(text) => setState((current) => editFile(current, text))}
            readOnly={openPath === null}
            label={openPath ?? t("pickFile")}
          />
        </section>

        <section className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-xl border">
          <header className="border-border flex items-center justify-between gap-2 border-b px-3 py-2">
            <span className="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
              <Eye className="h-3.5 w-3.5" />
              {t("preview")}
            </span>
            <div className="flex items-center gap-0.5">
              <DeviceButton
                on={device === "desktop"}
                label={t("desktop")}
                onClick={() => setDevice("desktop")}
              >
                <Monitor className="h-3.5 w-3.5" />
              </DeviceButton>
              <DeviceButton
                on={device === "mobile"}
                label={t("mobile")}
                onClick={() => setDevice("mobile")}
              >
                <Smartphone className="h-3.5 w-3.5" />
              </DeviceButton>
            </div>
          </header>
          {frame ? (
            <div className="min-h-0 flex-1 overflow-auto bg-white">
              <iframe
                title={t("preview")}
                src={frame.src}
                // No `allow-same-origin`: the page is the CMS's, and giving it
                // this origin would let it read the console's session.
                sandbox="allow-scripts allow-forms allow-popups"
                style={{ width: frame.width ?? "100%" }}
                className={cn("h-full border-0", frame.width ? "mx-auto block" : "w-full")}
              />
            </div>
          ) : (
            <p className="text-muted-foreground p-4 text-xs">{t("noPreview")}</p>
          )}
        </section>
      </div>

      {diff ? (
        <DiffCard
          diff={diff}
          counts={counts}
          before={savedText}
          after={openText}
          openPath={openPath}
          busy={busy}
          onDiscard={discard}
        />
      ) : null}

      <div className="border-border bg-card flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2">
        <span className="text-muted-foreground text-xs">
          {t("branch", { branch })} ·{" "}
          {t("changeSummary", {
            added: counts.added,
            modified: counts.modified,
            deleted: counts.deleted,
          })}
          {dirty.length > 0 ? ` · ${t("unsaved", { count: dirty.length })}` : ""}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={pull} disabled={busy !== null}>
            <ArrowDownToLine className="h-4 w-4" />
            {t("pull")}
          </Button>
          <Button variant="outline" size="sm" onClick={save} disabled={busy !== null || state.changes.length === 0}>
            <Save className="h-4 w-4" />
            {t("saveDraft")}
          </Button>
          <Button variant="outline" size="sm" onClick={check} disabled={busy !== null || state.changes.length === 0}>
            <ShieldCheck className="h-4 w-4" />
            {t("verify")}
          </Button>
          <Button size="sm" onClick={publish} disabled={busy !== null || !verify?.ok}>
            <Rocket className="h-4 w-4" />
            {t("publish")}
          </Button>
        </div>
      </div>

      {notice ? <p className="text-muted-foreground px-1 text-xs">{notice}</p> : null}
      {verify && !verify.ok ? (
        <div className="border-destructive/40 bg-destructive/5 text-destructive flex items-start gap-2 rounded-xl border px-3 py-2 text-xs">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {t("verifyFailedDetail", { files: verify.failures.join("、") })}
            {verify.detail ? ` — ${verify.detail}` : ""}
          </span>
        </div>
      ) : null}
      {error ? (
        <div className="border-destructive/40 bg-destructive/5 text-destructive rounded-xl border px-3 py-2 text-xs">
          {error}
        </div>
      ) : null}

      {plan ? (
        <PublishSheet
          plan={plan}
          siteTitle={siteTitle}
          siteAddress={siteAddress}
          busy={busy === "release"}
          onCancel={() => setPlan(null)}
          onConfirm={confirmRelease}
        />
      ) : null}
    </div>
  );
}

function DeviceButton({
  on,
  label,
  onClick,
  children,
}: {
  on: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={on}
      className={cn(
        "text-muted-foreground hover:bg-accent rounded-md p-1",
        on && "bg-accent text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function FileTree({
  nodes,
  openPath,
  onOpen,
  dirty,
  depth = 0,
}: {
  nodes: ReturnType<typeof buildTree>;
  openPath: string | null;
  onOpen: (path: string) => void;
  dirty: string[];
  depth?: number;
}) {
  return (
    <ul className="space-y-0.5">
      {nodes.map((node) =>
        node.kind === "dir" ? (
          <li key={node.path}>
            <div className="text-muted-foreground px-2 py-1 text-[11px] font-semibold tracking-wide uppercase">
              {node.name}
            </div>
            <FileTree nodes={node.children} openPath={openPath} onOpen={onOpen} dirty={dirty} depth={depth + 1} />
          </li>
        ) : (
          <li key={node.path}>
            <button
              type="button"
              onClick={() => onOpen(node.path)}
              style={{ paddingLeft: 8 + depth * 8 }}
              className={cn(
                "hover:bg-accent flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left font-mono text-[12px]",
                openPath === node.path && "bg-accent",
              )}
            >
              <FileCode2 className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{node.name}</span>
              {dirty.includes(node.path) ? <span className="text-foreground ml-auto text-[11px]">*</span> : null}
              {node.status ? (
                <span
                  className={cn(
                    "ml-auto rounded px-1 text-[10px] font-semibold",
                    node.status === "A" && "bg-emerald-500/15 text-emerald-600",
                    node.status === "M" && "bg-amber-500/15 text-amber-600",
                    node.status === "D" && "bg-rose-500/15 text-rose-600",
                  )}
                >
                  {node.status}
                </span>
              ) : null}
            </button>
          </li>
        ),
      )}
    </ul>
  );
}

function DiffCard({
  diff,
  counts,
  before,
  after,
  openPath,
  busy,
  onDiscard,
}: {
  diff: Diff;
  counts: { added: number; modified: number; deleted: number };
  before: string;
  after: string;
  openPath: string | null;
  busy: string | null;
  onDiscard: (path: string) => void;
}) {
  const t = useTranslations("sites");
  const rows = changedFiles(diff.changes);

  return (
    <div className="border-border bg-card max-h-64 overflow-auto rounded-xl border">
      <header className="border-border flex items-center gap-2 border-b px-3 py-2">
        <GitCompare className="h-4 w-4" />
        <span className="text-xs font-medium">{t("diffTitle")}</span>
        <span className="text-muted-foreground ml-auto text-[11px]">
          +{counts.added} ~{counts.modified} −{counts.deleted}
        </span>
      </header>
      <ul className="divide-border divide-y">
        {rows.map((change) => (
          <li key={change.path} className="flex items-center gap-2 px-3 py-1.5 font-mono text-[12px]">
            <span
              className={cn(
                "w-4 text-center font-semibold",
                change.status === "A" && "text-emerald-600",
                change.status === "M" && "text-amber-600",
                change.status === "D" && "text-rose-600",
              )}
            >
              {change.status}
            </span>
            <span className="truncate">{change.path}</span>
            <button
              type="button"
              onClick={() => onDiscard(change.path)}
              disabled={busy !== null}
              title={t("discard")}
              aria-label={`${t("discard")} ${change.path}`}
              className="text-muted-foreground hover:text-foreground ml-auto rounded p-1 disabled:opacity-40"
            >
              <Undo2 className="h-3.5 w-3.5" />
            </button>
          </li>
        ))}
        {rows.length === 0 ? (
          <li className="text-muted-foreground px-3 py-2 text-xs">{t("noChanges")}</li>
        ) : null}
      </ul>
      {openPath && before !== after ? (
        <div className="border-border border-t p-2">
          <LineDiff
            before={before}
            after={after}
            identicalLabel={t("diffIdentical")}
            unchangedLabel={(count) => t("diffUnchanged", { count })}
            tooLongLabel={t("diffTooLong")}
          />
        </div>
      ) : null}
      {diff.skipped.length > 0 ? (
        <p className="text-muted-foreground border-border border-t px-3 py-1.5 text-[11px]">
          {t("diffSkipped", { count: diff.skipped.length })}
        </p>
      ) : null}
    </div>
  );
}

function PublishSheet({
  plan,
  siteTitle,
  siteAddress,
  busy,
  onCancel,
  onConfirm,
}: {
  plan: ReleasePlan;
  siteTitle: string;
  siteAddress: string | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useTranslations("sites");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-card w-full max-w-lg rounded-2xl border p-5 shadow-xl">
        <h2 className="text-base font-semibold">{t("sheetTitle", { site: siteTitle })}</h2>
        <p className="text-muted-foreground mt-1 text-xs">
          {t("sheetHint")}
          {siteAddress ? ` · ${siteAddress}` : ""}
        </p>

        <ul className="mt-3 max-h-40 overflow-auto text-xs">
          {plan.files.map((file) => (
            <li key={file} className="truncate font-mono">
              {file}
            </li>
          ))}
        </ul>

        {plan.foreignDrafts.length > 0 ? (
          <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
            <b>{t("foreignTitle", { count: plan.foreignDrafts.length })}</b>
            <p className="mt-1 font-mono">{plan.foreignDrafts.join("、")}</p>
          </div>
        ) : null}

        {plan.blockers.length > 0 ? (
          <div className="border-destructive/40 bg-destructive/5 text-destructive mt-3 rounded-lg border px-3 py-2 text-xs">
            {plan.blockers.map((blocker) => (
              <p key={blocker.code}>
                {blocker.message}
                <span className="ml-1 opacity-70">（{recoveryFor(blocker.code).label}）</span>
              </p>
            ))}
          </div>
        ) : null}

        <div className="mt-4 flex items-center justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onCancel}>
            {t("cancel")}
          </Button>
          <Button size="sm" onClick={onConfirm} disabled={!plan.ok || busy}>
            <CheckCircle2 className="h-4 w-4" />
            {t("confirmPublish")}
          </Button>
        </div>
      </div>
    </div>
  );
}
