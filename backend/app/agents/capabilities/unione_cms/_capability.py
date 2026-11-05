"""Editing a Unione CMS site's web code, as a capability.

The CMS keeps a site's templates, layouts and static assets in its database.
This capability is how an agent works on them: pull the code into the
workspace, change files with the tools it already has, then verify, push and
release through the CMS's own endpoints.

Two things about it are deliberate.

**It is site-scoped at build time, not at call time.** `site_id` is
configuration, and so is the extension allow-list. A model that could name a
site per call could name somebody else's; a model that could name an extension
could write a `.java` file into the workspace and have it land in a build. Both
are fixed when the agent is published, which is also where a person can see
what they agreed to.

**It writes to a live system.** `side_effecting=True` is not paperwork: it is
what makes a binding's approval gate able to hold `cms_release` while the
read-only steps run unattended. The flow is split into eight tools precisely so
that gate can be placed between "saved as a draft" and "visitors can see it".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT

from app.agents.capabilities.unione_cms._toolset import UnioneCmsToolset
from app.cms.runtime import CmsRuntime


@dataclass
class UnioneCms(AbstractCapability[AgentDepsT]):
    """Tools for editing one CMS site's web code.

    `runtime` is supplied by the runner, not by the spec: it holds the
    person's platform token and the workspace for this site, and neither may
    travel through an agent's configuration.
    """

    site_id: int
    runtime: CmsRuntime | None = None

    _toolset: UnioneCmsToolset[AgentDepsT] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def get_toolset(self) -> UnioneCmsToolset[AgentDepsT]:
        """The CMS tools, built once per capability instance."""
        if self._toolset is None:
            if self.runtime is None:
                raise RuntimeError(
                    "the Unione CMS capability needs a runtime bound to a signed-in "
                    "person and a site; the runner supplies it per run"
                )
            self._toolset = UnioneCmsToolset[AgentDepsT](self.runtime)
        return self._toolset
