import { defineRouting } from "next-intl/routing";

import { locales, defaultLocale, matchLocale, type Locale } from "@/i18n";

/**
 * The cookie holding the locale the visitor picked.
 *
 * `NEXT_LOCALE` is next-intl's own default name, written client-side by the
 * navigation APIs below, and read back - and written, for a path that names a
 * locale - by `src/middleware.ts`. It is named here rather than left to the
 * default so that every write and the read are the same constant: a divergence
 * between them is invisible - the switch appears to work,
 * because the URL prefix carries the locale for exactly as long as the current
 * page lasts.
 */
export const LOCALE_COOKIE_NAME = "NEXT_LOCALE";

/** A year. The choice is a preference, not a session. */
export const LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

/**
 * One routing configuration, shared by the middleware and the navigation APIs.
 *
 * `localePrefix: "as-needed"` - English, the default, has no prefix; every other
 * locale lives under its own, `/pl/...` and `/de/...`.
 *
 * `localeDetection: true` because the product's language is the one the system
 * already speaks: a Chinese Windows or a Polish browser opens the console in its
 * own language instead of English, and the switcher remains for anybody who wants
 * another one. next-intl resolves the `accept-language` header against `locales`
 * by best fit, so `zh-Hans-CN` lands on `zh-CN` and `zh-TW` on `zh-TW`.
 *
 * A locale in the path still wins over both the cookie and the header - the URL
 * is what a shared link means - and `src/middleware.ts` keeps the cookie half for
 * the unprefixed paths that next-intl does not write, see the notes there.
 */
export const routing = defineRouting({
  locales,
  defaultLocale,
  localePrefix: "as-needed",
  localeDetection: true,
  localeCookie: { name: LOCALE_COOKIE_NAME, maxAge: LOCALE_COOKIE_MAX_AGE },
});

/**
 * The locale a path names in its first segment, or `null` when it names none.
 *
 * Matched case-insensitively, because next-intl's own prefix matching is:
 * `/PL/agents` is a Polish path to it, redirected to the canonical `/pl/agents`.
 * Reading it as unprefixed here would put a second prefix on the front of a path
 * that already has one - `/pl/PL/agents`, which no route serves.
 *
 * The match returns the catalog's spelling rather than the path's, so a
 * `/zh-cn/agents` bookmark is remembered as `zh-CN`: the cookie outlives the URL,
 * and a cache keyed on `zh-cn` is a second copy of the catalog.
 */
export function localePrefixOf(pathname: string): Locale | null {
  return matchLocale(pathname.split("/")[1]);
}

/** The locale the visitor picked, when this deployment serves it and it is not the default. */
export function pickedLocale(value: string | undefined): Locale | null {
  const match = matchLocale(value);
  return match === null || match === defaultLocale ? null : match;
}
