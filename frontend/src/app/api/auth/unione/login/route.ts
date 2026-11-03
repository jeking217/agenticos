import { type NextRequest } from "next/server";

import { unioneSignIn } from "@/lib/unione-bff";

/** The platform's account and password, relayed - the session is written to cookies. */
export async function POST(request: NextRequest) {
  return unioneSignIn(request, "/api/v1/auth/unione/login");
}
