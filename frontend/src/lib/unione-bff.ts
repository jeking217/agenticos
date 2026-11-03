/**
 * The BFF half of a Unione sign-in (#2101).
 *
 * The backend answers with this deployment's tokens; the browser needs them as
 * HttpOnly cookies, and only a route handler can set those. So every route under
 * `/api/auth/unione/*` is a thin hop: forward the request, then either set the
 * cookie pair or pass the refusal through untouched.
 *
 * Bodies are forwarded as the bytes they arrived as, never re-parsed. The
 * backend's schema is the one that refuses a bad body, and a second copy of its
 * rules in this hop would eventually disagree with it - the same reason the
 * directory sign-in's proxy picks fields out of the JSON instead of validating.
 *
 * Refusals are passed through whole rather than translated: the platform's
 * sentence - wrong password, the account is locked until 14:05, the ticket has
 * already been used - is the only one that knows what actually went wrong, and a
 * code minted here would be a second, vaguer answer.
 */

import { type NextRequest, NextResponse } from "next/server";

import {
  BackendApiError,
  backendFetch,
  bffJson,
  bffRefusal,
  forwardedFor,
  forwardRateLimit,
} from "@/lib/server-api";
import { ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE, secureCookies } from "@/lib/session-cookie";
import type { LoginResponse } from "@/types";

/** The cookie lifetime, in seconds, the password login beside this one uses. */
const ACCESS_MAX_AGE = 60 * 15;
const REFRESH_MAX_AGE = 60 * 60 * 24 * 7;

/** A refusal from the backend, forwarded as the envelope the client reads. */
function refusal(error: unknown): NextResponse {
  if (error instanceof BackendApiError) {
    if (error.status === 429) return forwardRateLimit(error);
    return bffJson(error.data ?? { code: "LOGIN_FAILED" }, { status: error.status });
  }
  return bffRefusal("INTERNAL_SERVER_ERROR", 500);
}

/**
 * Sign in on the backend, then hand the browser the session as cookies.
 *
 * The access token is echoed in the body as well, as every sign-in route here
 * does: the chat socket authenticates with it cross-origin, and an HttpOnly
 * cookie is not something JavaScript can read.
 */
export async function unioneSignIn(
  request: NextRequest,
  backendPath: string,
): Promise<NextResponse> {
  try {
    const data = await backendFetch<LoginResponse>(backendPath, {
      method: "POST",
      headers: { ...forwardedFor(request) },
      body: await request.text(),
    });

    const user = await backendFetch("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${data.access_token}` },
    });

    const response = bffJson({ user, access_token: data.access_token });
    response.cookies.set(ACCESS_TOKEN_COOKIE, data.access_token, {
      httpOnly: true,
      secure: secureCookies(request),
      sameSite: "lax",
      maxAge: ACCESS_MAX_AGE,
      path: "/",
    });
    response.cookies.set(REFRESH_TOKEN_COOKIE, data.refresh_token, {
      httpOnly: true,
      secure: secureCookies(request),
      sameSite: "lax",
      maxAge: REFRESH_MAX_AGE,
      path: "/",
    });
    return response;
  } catch (error) {
    return refusal(error);
  }
}

/**
 * A hop that carries no session: the login page's questions about the bridge, the
 * captcha image, and the request that texts a code.
 *
 * The caller's address rides along, so the backend's per-address rate limit sees
 * the person rather than this container.
 */
export async function unioneHop(
  request: NextRequest,
  backendPath: string,
  method: "GET" | "POST",
): Promise<NextResponse> {
  try {
    const data = await backendFetch<unknown>(backendPath, {
      method,
      headers: { ...forwardedFor(request) },
      ...(method === "POST" ? { body: await request.text() } : {}),
    });
    return bffJson(data);
  } catch (error) {
    return refusal(error);
  }
}
