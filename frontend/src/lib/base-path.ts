/**
 * The path this build is mounted at, or an empty string at the root.
 *
 * Fixed when `next build` runs (see `next.config.ts`), so this is a build-time
 * constant rather than a deployment setting - it reads the `NEXT_PUBLIC_` copy
 * of the same value Next compiled in, which is also what makes it usable from
 * client components.
 *
 * Needed wherever this app addresses itself. `next/link`, `router.push` and
 * `redirect` add the prefix on their own, but a bare `fetch` of an API path is
 * only a string, and on a sub-path deployment it reaches the *host's* root
 * instead of this application.
 */
export const BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/+$/, "");

/** Prefix a path this app serves with the segment it is mounted under. */
export function withBasePath(path: string): string {
  return `${BASE_PATH}${path}`;
}

/**
 * Remove the segment this build is mounted under, when the path carries it.
 *
 * A route handler sees the path with or without it depending on where Next
 * strips it, and a handler that guesses wrong forwards `/agent/api/...` to
 * the backend as though it were API path. Strip-if-present is right either way;
 * the boundary check keeps `/agents` from being read as the prefix.
 */
export function stripBasePath(pathname: string): string {
  if (!BASE_PATH) return pathname;
  if (pathname === BASE_PATH) return "/";
  if (pathname.startsWith(`${BASE_PATH}/`)) return pathname.slice(BASE_PATH.length);
  return pathname;
}
