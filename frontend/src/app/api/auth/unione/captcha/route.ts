import { type NextRequest } from "next/server";

import { unioneHop } from "@/lib/unione-bff";

/** The platform's captcha, as a data URL and the id its answer must quote. */
export async function GET(request: NextRequest) {
  return unioneHop(request, "/api/v1/auth/unione/captcha", "GET");
}
