"use client";

/**
 * The drama app, as a destination in the console's column (#2302).
 *
 * Not a link: Unione AI Drama is a program on this computer, not a page here,
 * and a `Link` to it would be a 404 with extra steps. Clicking asks the backend
 * for a ticket minted for the person signed in, then asks the shell to start the
 * app with that ticket - which signs it in without a second password.
 *
 * Three things can go wrong and each is said in its own words, because the fix
 * is different for each: the app is not installed (install it), the shell could
 * not start it (the path is wrong, or the binary is broken), or no ticket was
 * minted (this deployment could not be reached, and the app can still be opened
 * by hand and signed in to).
 *
 * Only drawn in the desktop shell. In a browser there is no local program to
 * start and no shell to start it with, so an entry that could only fail would be
 * a broken button on every deployment's website.
 */

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Clapperboard, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMounted } from "@/hooks/use-mounted";
import { launchDramaApp, type DramaLaunchOutcome } from "@/lib/desktop/drama";
import { isDesktopShell, readShellInfo } from "@/lib/desktop/shell";
import { cn } from "@/lib/utils";

/**
 * Where a person without the app gets it. Deployment configuration rather than a
 * constant: an air-gapped install has an internal file server, and a public one
 * has a download page, and neither is the other's address. Empty means this
 * deployment publishes none, and the dialog asks the person to get it from their
 * administrator instead of sending them to a dead link.
 */
const DOWNLOAD_URL = (process.env.NEXT_PUBLIC_DRAMA_DOWNLOAD_URL ?? "").trim();

export function DramaEntry({ collapsed = false }: { collapsed?: boolean }) {
  const t = useTranslations("nav");
  const mounted = useMounted();
  const [opening, setOpening] = useState(false);
  const [outcome, setOutcome] = useState<DramaLaunchOutcome | null>(null);

  // Read after mount: the shell announces itself on the client, and drawing the
  // entry on the server and not on the client is a hydration mismatch.
  const shell = mounted ? readShellInfo() : null;
  if (!isDesktopShell(shell)) return null;

  async function open() {
    if (opening) return;
    setOpening(true);
    try {
      const result = await launchDramaApp();
      setOutcome(result.status === "launched" ? null : result);
    } finally {
      setOpening(false);
    }
  }

  return (
    <>
      <div className={cn("mt-2 border-t pt-2", collapsed ? "w-9" : "mx-1")}>
        <button
          type="button"
          onClick={open}
          disabled={opening}
          title={collapsed ? t("drama") : undefined}
          aria-label={collapsed ? t("drama") : undefined}
          className={cn(
            "text-foreground/90 hover:bg-accent/60 hover:text-foreground touch:min-h-11 relative flex items-center rounded-xl text-sm font-medium transition-colors disabled:opacity-60",
            collapsed ? "h-9 w-9 justify-center" : "h-9 w-full gap-2.5 px-2.5",
          )}
        >
          {opening ? (
            <Loader2
              className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none"
              aria-hidden
            />
          ) : (
            <Clapperboard className="h-4 w-4 shrink-0" strokeWidth={1.7} aria-hidden />
          )}
          {collapsed ? null : <span className="truncate">{t("drama")}</span>}
        </button>
      </div>

      <DramaDialog outcome={outcome} onClose={() => setOutcome(null)} />
    </>
  );
}

/** What each failure has to say, in the words the person needs for that failure. */
function DramaDialog({
  outcome,
  onClose,
}: {
  outcome: DramaLaunchOutcome | null;
  onClose: () => void;
}) {
  const copy = useTranslations("desktop.drama");
  const t = useTranslations("nav");
  const [copied, setCopied] = useState(false);

  if (!outcome || outcome.status === "launched") return null;

  const close = (next: boolean) => {
    if (!next) onClose();
  };

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(DOWNLOAD_URL);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // No clipboard (an insecure origin, a permission the platform keeps): the
      // address is on screen and can be selected by hand, which is why it is there.
    }
  }

  if (outcome.status === "not-installed") {
    return (
      <Dialog open onOpenChange={close}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{copy("notInstalledTitle")}</DialogTitle>
            <DialogDescription>{copy("notInstalledBody")}</DialogDescription>
          </DialogHeader>
          <ol className="text-muted-foreground list-decimal space-y-1.5 pl-5 text-sm">
            <li>{copy("stepDownload")}</li>
            <li>{copy("stepInstall")}</li>
            <li>{copy("stepRetry", { app: t("drama") })}</li>
          </ol>
          {DOWNLOAD_URL ? (
            <a
              href={DOWNLOAD_URL}
              target="_blank"
              rel="noreferrer"
              className="text-primary text-sm break-all underline underline-offset-2"
            >
              {DOWNLOAD_URL}
            </a>
          ) : (
            <p className="text-muted-foreground text-sm">{copy("noDownload")}</p>
          )}
          <DialogFooter>
            {DOWNLOAD_URL ? (
              <Button variant="outline" onClick={copyLink}>
                {copied ? copy("copied") : copy("copyLink")}
              </Button>
            ) : null}
            <Button onClick={onClose}>{copy("close")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  const ticketed = outcome.status === "ticket-failed";
  return (
    <Dialog open onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{ticketed ? copy("ticketTitle") : copy("failedTitle")}</DialogTitle>
          <DialogDescription>
            {ticketed ? copy("ticketBody") : copy("failedBody")}
          </DialogDescription>
        </DialogHeader>
        {!ticketed && outcome.detail ? (
          <p className="text-muted-foreground font-mono text-xs break-all">{outcome.detail}</p>
        ) : null}
        <DialogFooter>
          <Button onClick={onClose}>{copy("close")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
