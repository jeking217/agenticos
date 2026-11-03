import { type NextRequest } from "next/server";

import { unioneSignIn } from "@/lib/unione-bff";

/** The code the platform texted, exchanged for a session. */
export async function POST(request: NextRequest) {
  return unioneSignIn(request, "/api/v1/auth/unione/login-sms");
}
