import { type NextRequest } from "next/server";

import { unioneHop } from "@/lib/unione-bff";

/**
 * What the login page needs to know before drawing a form: is the bridge
 * configured, does the platform want a captcha, and does the local form stay.
 *
 * Answered rather than 404'd when the bridge is off, so the page never has to
 * guess from an error which sign-in it should be showing.
 */
export async function GET(request: NextRequest) {
  return unioneHop(request, "/api/v1/auth/unione/config", "GET");
}
