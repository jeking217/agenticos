import { type NextRequest } from "next/server";

import { unioneSignIn } from "@/lib/unione-bff";

/**
 * The one-time ticket the platform's portal redirected here with, traded for a
 * session.
 *
 * `no-store` matters here beyond the usual: the ticket is spent by the platform
 * whether or not it was still valid, so a cached answer would be an answer that
 * cannot be repeated.
 */
export async function POST(request: NextRequest) {
  return unioneSignIn(request, "/api/v1/auth/unione/once");
}
