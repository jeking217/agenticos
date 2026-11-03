import { type NextRequest } from "next/server";

import { unioneHop } from "@/lib/unione-bff";

/** Text a sign-in code to a phone number, through the platform's SMS gateway. */
export async function POST(request: NextRequest) {
  return unioneHop(request, "/api/v1/auth/unione/captcha/sms", "POST");
}
