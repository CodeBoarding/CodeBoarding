"""Source resolution for analysis workflows.

A *source* materializes a local repository path that a scope workflow
(full / incremental / partial) can run against. Local sources just wrap an
existing path; remote sources clone, probe the public cache, and handle
upload + cleanup on exit.
"""

from codeboarding_workflows.sources.local import SourceContext, local_source
from codeboarding_workflows.sources.remote import onboarding_materials_exist, remote_source

__all__ = ["SourceContext", "local_source", "remote_source", "onboarding_materials_exist"]
