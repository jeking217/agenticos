"""Web-site programming against a Unione CMS.

The CMS stores a site's web code in its database, not on disk and not in git.
This package is the one place that speaks to it: fetching a site's code into a
workspace, working out what changed, saving it back as drafts, and publishing
those drafts - each step a separate, refusable operation rather than one
"sync" call whose failure leaves nobody sure what happened.

The capability in `app.agents.capabilities.unione_cms` is a thin adapter over
this package. Keeping the split means the rules are testable without a model,
and the model cannot reach around them.
"""

from app.cms.errors import CmsError

__all__ = ["CmsError"]
