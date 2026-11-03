/**
 * Signing in through the Unione platform (#2101).
 *
 * The platform owns the accounts, so the login page asks this deployment two
 * questions before it draws anything - is the bridge configured, and does the
 * platform want a captcha - and then either offers the platform's form or the
 * local one. Nothing here decides anything; the answers come from
 * `/auth/unione/config`, and the only value this module interprets is the ticket
 * the platform's portal puts on the URL it redirects to.
 */

import { apiClient } from "@/lib/api-client";
import type { UnioneCaptcha, UnioneConfig } from "@/types";

/**
 * The prefix the platform gives every one-time ticket.
 *
 * Checked before the value is sent anywhere: the login page is reachable by
 * anyone with any `?accessToken=`, and spending a round trip on each one is a
 * way to make this deployment the thing that probes the platform.
 */
export const UNIONE_TICKET_PREFIX = "tk-";

/** The one-time ticket on the current URL, or null when there is not one. */
export function ticketFrom(search: string): string | null {
  const value = new URLSearchParams(search).get("accessToken");
  return value && value.startsWith(UNIONE_TICKET_PREFIX) ? value : null;
}

/** Whether this deployment bridges to the platform, and how it wants the form. */
export async function loadUnioneConfig(): Promise<UnioneConfig> {
  return apiClient.get<UnioneConfig>("/auth/unione/config");
}

/**
 * A fresh captcha. Busted past any cache: the platform's answer is filed under a
 * new id every time, so a cached image would be an image whose answer is already
 * spent.
 */
export async function loadUnioneCaptcha(): Promise<UnioneCaptcha> {
  return apiClient.get<UnioneCaptcha>(`/auth/unione/captcha?t=${Date.now()}`);
}

/** Ask the platform to text a sign-in code to this number. */
export async function sendUnioneSmsCaptcha(phone: string): Promise<void> {
  await apiClient.post("/auth/unione/captcha/sms", { phone });
}
