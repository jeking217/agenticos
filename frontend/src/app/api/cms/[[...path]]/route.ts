import { platformProxy } from "@/lib/platform-proxy";

// Every CMS call the console makes: status, sites, pull/diff/verify/push,
// discard, release, rollback, suggestions - and the preview proxy, whose
// response is the CMS's own HTML rather than JSON. One forwarder for all of
// them, because they differ in nothing but the path, and because the debug
// cookie must never leave the server: `/api/cms/preview/...` is what the
// iframe loads, this route attaches the cookie, and the browser sees only the
// rendered page.
export const { GET, POST, PUT, PATCH, DELETE } = platformProxy();
