import { type NextRequest } from "next/server";

import { BackendApiError, backendFetch, bffJson } from "@/lib/server-api";

/**
 * Where the drama app spends the ticket the console handed it (#2302).
 *
 * Public, and it has to be: the app calling this has no session here yet - the
 * ticket is its credential, which is why the backend spends it exactly once.
 *
 * The backend answers in the platform's envelope, refusals included: `success:
 * false` with a sentence written for the person, in HTTP 200, which is what the
 * app already knows how to read from the Unione portal. That sentence is passed
 * through untouched. This hop writes none of its own: the app draws its own
 * words for "no ticket", and a translated sentence would be a second answer to
 * a question the backend already answered - one that could disagree with it.
 */
export async function GET(request: NextRequest) {
  const accessToken = request.nextUrl.searchParams.get("accessToken")?.trim() ?? "";
  if (!accessToken) {
    return bffJson({ success: false, code: 400 }, { status: 400 });
  }
  try {
    const data = await backendFetch<unknown>("/api/v1/drama/access", {
      params: { accessToken },
      cache: "no-store",
    });
    return bffJson(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      if (error.status >= 500) {
        return bffJson({ success: false, code: 502 }, { status: 502 });
      }
      const body = error.data;
      if (body && typeof body === "object" && "success" in body) {
        return bffJson(body);
      }
      return bffJson({ success: false, code: error.status });
    }
    return bffJson({ success: false, code: 502 }, { status: 502 });
  }
}
