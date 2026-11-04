import { type NextRequest } from "next/server";

import { BackendApiError, backendFetch, bffJson, bffRefusal } from "@/lib/server-api";
import { ACCESS_TOKEN_COOKIE } from "@/lib/session-cookie";

/**
 * A ticket for the drama app on this computer, minted for the caller (#2302).
 *
 * Behind a session for the obvious reason: the ticket signs the app in as
 * somebody, and the only somebody the console may speak for is the person whose
 * cookie this is. The backend binds it to that account; this hop only carries it.
 *
 * `no-store`, because a ticket answered from a cache would be a ticket already
 * spent by the time it arrived.
 */
export async function POST(request: NextRequest) {
  const accessToken = request.cookies.get(ACCESS_TOKEN_COOKIE)?.value;
  if (!accessToken) return bffRefusal("NOT_AUTHENTICATED", 401);
  try {
    const data = await backendFetch<unknown>("/api/v1/drama/tickets", {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: "no-store",
    });
    return bffJson(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return bffJson({ detail: error.message }, { status: error.status });
    }
    return bffRefusal("INTERNAL_SERVER_ERROR", 500);
  }
}
