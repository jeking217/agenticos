"""Why a CMS call failed, in terms a person can act on.

Every failure the platform surfaces carries a stable `code`, because the
difference between "your session ended" and "somebody else published while you
were editing" decides what the button says. A bare 500 from the CMS reaches the
console as a stack trace and reaches the model as an unhelpful sentence it will
try again.

The codes are the vocabulary of the whole feature - the API returns them, the
console translates them, and the capability hands them to the model as text.
"""

from __future__ import annotations


class CmsError(Exception):
    """A refusal or a failure from the CMS, with a code the UI can key on."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"


#: The person's platform session is gone. Signing in again fixes it; retrying
#: does not, which is why this is its own code rather than a 401 handed upwards.
TOKEN_EXPIRED = "cms_token_expired"
#: Signed in, but not allowed to change this site. `cms_site_list` decides this
#: up front so the console can disable the door rather than open it and refuse.
FORBIDDEN = "cms_forbidden"
#: The site or resource is not there any more. Somebody deleted it, or the
#: binding points at a site that was never in this tenant.
NOT_FOUND = "cms_not_found"
#: The resource is published and must be upgraded before it can be edited.
PUBLISHED = "cms_resource_published"
#: Beetl rejected the template. Carries the line the parser named.
VERIFY_FAILED = "cms_verify_failed"
#: The CMS is up but answered with something this client does not understand.
UNEXPECTED = "cms_unexpected_response"
#: Nothing answered at the configured address.
UNREACHABLE = "cms_unreachable"
#: A path that would escape the workspace, or an extension outside the allowed
#: set. Refused before any bytes are written.
PATH_REJECTED = "cms_path_rejected"
#: Everything upstream was fine, but the site's state moved under us - somebody
#: else published between our pull and our push.
DRIFT = "cms_drift"
