import createMiddleware from "next-intl/middleware";
import { NextRequest, NextResponse } from "next/server";

import {
  LOCALE_COOKIE_MAX_AGE,
  LOCALE_COOKIE_NAME,
  localePrefixOf,
  pickedLocale,
  routing,
} from "./lib/locale-routing";
import { BASE_PATH, stripBasePath } from "./lib/base-path";
import { readPublicConfig } from "./lib/public-config";
import { contentSecurityPolicyHeader } from "./lib/security-headers";

const handleI18nRouting = createMiddleware(routing);

/**
 * Redirect to a path that already carries the deployment's segment.
 *
 * Not `request.nextUrl.clone()`: a cloned URL keeps `basePath` on its own and
 * puts it back on the way out, so assigning a pathname that *also* starts with
 * the segment produced `/agent/agent/en`. Composing from the origin
 * emits the path verbatim, which is what a caller that has prefixed it wants.
 */
function redirectTo(request: NextRequest, path: string): NextResponse {
  // The query goes back on too: `?create=1` on a link next-intl has to
  // re-prefix is part of what the visitor asked for.
  return NextResponse.redirect(
    new URL(`${path}${request.nextUrl.search}`, request.nextUrl.origin),
  );
}

/**
 * Send a request whose URL carries no locale to the one the visitor picked.
 *
 * With `localePrefix: "as-needed"` the prefix is the whole of the locale: a path
 * without one resolves to `defaultLocale`. So an ordinary `<Link href="/agents">`
 * or `router.push("/orgs")` - and there are dozens, none of them locale-aware -
 * silently switched the UI back to English, which is what #285 saw as the choice
 * "reverting" on the next navigation.
 *
 * `localeDetection` is on, so next-intl resolves an unprefixed path from the
 * cookie and then from `accept-language`. This runs first anyway: it is the one
 * place where "the locale a person picked" is answered the same way for every
 * path, and it keeps the redirect out of next-intl's hands when the two disagree
 * about the default.
 */
function restorePickedLocale(request: NextRequest): NextResponse | null {
  const pathname = stripBasePath(request.nextUrl.pathname);
  if (localePrefixOf(pathname)) return null;

  const picked = pickedLocale(request.cookies.get(LOCALE_COOKIE_NAME)?.value);
  if (!picked) return null;

  // `pathname` here is the application-internal one; the address the browser
  // ends up on carries the deployment's segment, so it goes back on the way out.
  return redirectTo(
    request,
    `${BASE_PATH}/${picked}${pathname === "/" ? "" : pathname}`,
  );
}

/**
 * Record the locale a prefixed path names, so it outlives that path.
 *
 * next-intl writes the cookie itself only when the request carries none *and*
 * `accept-language` disagrees with the locale it resolved. So a Polish browser
 * opening a shared `/pl/agents` persists nothing, and its next ordinary `<Link>`
 * is English again - #285 arriving by URL rather than by the switcher, for the
 * visitors most likely to want Polish.
 *
 * Document requests only. A background request is how the router revalidates a
 * route of the locale the visitor has just left, so writing from one would undo
 * the switch they have just made; next-intl's `syncCookie` reads the same header
 * for the same reason.
 */
function rememberPrefixedLocale(request: NextRequest, response: NextResponse): void {
  if ((request.headers.get("sec-fetch-dest") ?? "document") !== "document") return;

  const named = localePrefixOf(stripBasePath(request.nextUrl.pathname));
  if (!named || request.cookies.get(LOCALE_COOKIE_NAME)?.value === named) return;

  response.cookies.set(LOCALE_COOKIE_NAME, named, {
    path: "/",
    maxAge: LOCALE_COOKIE_MAX_AGE,
    sameSite: "lax",
  });
}

/**
 * A single-use, unguessable value for this request's `script-src 'nonce-…'`.
 *
 * 128 random bits, base64. Regenerated per request: a nonce reused across
 * responses is one an attacker can read from an earlier page and reuse, which is
 * `'unsafe-inline'` with extra steps (#1624).
 */
function generateNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

/**
 * Stamp the Content-Security-Policy, which names this deployment's public origins
 * and this request's script nonce.
 *
 * Here and not in `next.config.ts`, whose `headers()` runs at build: `connect-src`
 * has to allow `PUBLIC_API_URL` and `PUBLIC_WS_URL`, read from the server's
 * environment on every request (#1544), and the nonce cannot be a build constant.
 * The rest of the security headers are constants and stay in the config.
 */
function withContentSecurityPolicy(response: NextResponse, csp: string): NextResponse {
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export default function middleware(request: NextRequest): NextResponse {
  const nonce = generateNonce();
  const { value: csp } = contentSecurityPolicyHeader(readPublicConfig(process.env), nonce);

  const restored = restorePickedLocale(request);
  if (restored) return withContentSecurityPolicy(restored, csp);

  // Next stamps the nonce onto its own inline flight scripts when it finds it on
  // the *request's* Content-Security-Policy header, so it has to be forwarded to
  // the render, not only set on the response. `x-nonce` rides along for a server
  // component that renders a <script> of its own. next-intl forwards the headers
  // of the request it is handed, so the render sees both.
  request.headers.set("x-nonce", nonce);
  request.headers.set("Content-Security-Policy", csp);

  // Next hands middleware a pathname that still carries the deployment's
  // segment (`/agent/login`), while next-intl works on the
  // application-internal path and reads `nextUrl.basePath` to put the segment
  // back on every URL it emits. Leaving it in made next-intl read `agent` as
  // the locale and rewrite to `/en/agent/en/login` - every page 404.
  //
  // The request is edited in place rather than copied into a new NextRequest:
  // a fresh instance loses `nextUrl.basePath`, and then every redirect next-intl
  // emits drops the segment (`/agent/en/login` -> `/login`).
  if (BASE_PATH) {
    const internal = stripBasePath(request.nextUrl.pathname);

    // The bare segment is the one path next-intl cannot resolve for itself: it
    // builds its candidate URL from `new URL("/", request.url)`, which drops
    // the basePath, and then decides the rewrite it wants is the request
    // itself and emits none. Next is left with "/", and `[locale]` has no
    // match without a segment - a 404 on the deployment's own entry point.
    //
    // Rewritten, not redirected: `localePrefix: "as-needed"` sends a prefixed
    // `/en` straight back to `/`, so redirecting the entry point at the default
    // locale bounces between the two forever. A rewrite serves that page at the
    // address the visitor typed, which is what next-intl does for every other
    // unprefixed path.
    if (internal === "/") {
      return withContentSecurityPolicy(
        NextResponse.rewrite(
          new URL(
            `${BASE_PATH}/${routing.defaultLocale}${request.nextUrl.search}`,
            request.nextUrl.origin,
          ),
        ),
        csp,
      );
    }

    request.nextUrl.pathname = internal;
  }

  const response = handleI18nRouting(request);
  rememberPrefixedLocale(request, response);
  return withContentSecurityPolicy(response, csp);
}

export const config = {
  matcher: [
    // The bare root, listed on its own: under a `basePath` Next matches the
    // matcher against the path with the segment removed, and "/" then falls
    // outside the pattern below - so the entry point skipped middleware
    // entirely and 404'd instead of resolving a locale.
    "/",
    // Match all pathnames except for:
    // - /api (API routes)
    // - /_next (Next.js internals)
    // - /static (inside /public)
    // - /_vercel (Vercel internals)
    // - All root files like favicon.ico, robots.txt, etc.
    // - App-router metadata convention routes (icon, apple-icon, opengraph-image,
    //   twitter-image, manifest.*, robots, sitemap) - these are dotless URLs
    //   that Next.js generates from src/app/{icon,apple-icon,…}.tsx and would
    //   otherwise be redirected to /{locale}/icon → 404.
    "/((?!api|_next|_vercel|static|icon$|apple-icon$|opengraph-image$|twitter-image$|manifest|robots$|sitemap$|.*\\..*).*)",
  ],
};
