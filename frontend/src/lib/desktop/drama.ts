/**
 * Opening the drama app - Unione AI Drama - from the console (#2302).
 *
 * Two things have to happen and they happen in this order for a reason: the
 * console asks the backend for a ticket minted for the person signed in here,
 * and only then asks the shell to start the app with that ticket on its command
 * line. The reverse order would hand a process a ticket for somebody else, or
 * for nobody.
 *
 * What comes back is a status rather than a thrown error, because three of the
 * outcomes are things the person has to be told in their own words - the app is
 * not installed, the shell could not start it, the ticket could not be minted -
 * and only one of them is success.
 */

import { withBasePath } from "@/lib/base-path";
import { invokeShell, ShellError } from "@/lib/desktop/shell";

export type DramaLaunchOutcome =
  | { status: "launched" }
  /** No install to hand the ticket to: the dialog explains where to get one. */
  | { status: "not-installed" }
  /** The shell found the app but could not start it, or answered something unreadable. */
  | { status: "failed"; detail: string }
  /** No ticket: not signed in, the backend refused, or it could not be reached. */
  | { status: "ticket-failed"; detail: string };

/** What the shell's `open_drama` answers, narrowed from the unknown it returns. */
function decode(answer: unknown): DramaLaunchOutcome {
  if (typeof answer === "object" && answer !== null) {
    const { launched, reason } = answer as { launched?: unknown; reason?: unknown };
    if (launched === true) return { status: "launched" };
    if (reason === "not_installed") return { status: "not-installed" };
    if (typeof reason === "string" && reason.length > 0)
      return { status: "failed", detail: reason };
  }
  return { status: "failed", detail: "unreadable answer" };
}

function messageOf(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

/** A ticket minted for the caller, or the reason there is none. */
async function mintTicket(): Promise<string> {
  const response = await fetch(withBasePath("/api/drama/ticket"), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    // i18n-exempt: the detail of a thrown Error, logged and shown to nobody.
    throw new Error(`ticket request answered ${response.status}`);
  }
  const body: unknown = await response.json();
  const ticket = (body as { ticket?: unknown } | null)?.ticket;
  if (typeof ticket !== "string" || !ticket.startsWith("tk-")) {
    throw new Error("ticket request answered without a ticket");
  }
  return ticket;
}

/** Mint a ticket and start the app with it. Never throws; the status says how it went. */
export async function launchDramaApp(): Promise<DramaLaunchOutcome> {
  let ticket: string;
  try {
    ticket = await mintTicket();
  } catch (error) {
    return { status: "ticket-failed", detail: messageOf(error) };
  }

  try {
    return decode(await invokeShell("open_drama", { ticket }));
  } catch (error) {
    return {
      status: "failed",
      detail: error instanceof ShellError ? error.message : messageOf(error),
    };
  }
}
