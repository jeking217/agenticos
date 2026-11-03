"""Signing in through the Unione platform: the bridge into its accounts (#2101).

The platform owns the authentication; this package owns the translation. `client`
speaks the platform's protocol and returns an identity, `sign_in` turns that identity
into an account here, and the route module above them issues this deployment's own
session - so nothing downstream knows a platform was involved.
"""

from app.services.unione.client import UnioneClient, UnioneIdentity
from app.services.unione.sign_in import PROVIDER, UnioneSignInService, email_for

__all__ = [
    "PROVIDER",
    "UnioneClient",
    "UnioneIdentity",
    "UnioneSignInService",
    "email_for",
]
