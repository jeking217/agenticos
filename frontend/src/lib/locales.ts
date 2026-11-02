/**
 * Which locales exist, in one place both the app and the build config can read.
 *
 * `src/i18n.ts` pulls in `next-intl/server` and the whole English catalog, so
 * `next.config.ts` cannot import it to find out - which is how the moved-route
 * redirects came to hard-code `en|pl` and answer a German bookmark with a 404
 * while every other surface had been translated.
 */
export const locales = ["en", "pl", "de", "zh-CN", "zh-TW"] as const;

export type Locale = (typeof locales)[number];

export const defaultLocale: Locale = "en";

/**
 * The locale a string names, if this deployment serves it.
 *
 * Case-insensitive, because the two Chinese locales carry a region subtag and
 * the case of that subtag is a convention rather than a fact: `zh-CN` is what
 * the catalogs are called and what `hreflang` wants, while a share link may hold
 * `/zh-cn/agents` or a cookie written by an older build. Matching exactly would
 * report those as "no locale" - and a path with no locale is one the middleware
 * re-prefixes, which turns `/zh-cn/agents` into `/zh-CN/zh-cn/agents`.
 */
export function matchLocale(value: string | undefined | null): Locale | null {
  const asked = value?.trim().toLowerCase();
  if (!asked) return null;
  return locales.find((locale) => locale.toLowerCase() === asked) ?? null;
}
