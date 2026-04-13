"""Declarative registry of external tool dependencies.

Layered: registry (data) -> paths -> manifest / installers -> __init__ (re-exports).
"""

from .registry import (  # noqa: F401
    JDTLS_BUILD,
    JDTLS_URL_TEMPLATE,
    JDTLS_VERSION,
    PINNED_NODE_VERSION,
    PLATFORM_SUFFIX,
    TOOL_REGISTRY,
    TOOLS_REPO,
    TOOLS_TAG,
    ConfigSection,
    GitHubToolSource,
    PackageManagerToolSource,
    ProgressCallback,
    ToolDependency,
    ToolKind,
    ToolSource,
    UpstreamToolSource,
)
from .paths import (  # noqa: F401
    MINIMUM_NODE_MAJOR_VERSION,
    embedded_node_path,
    embedded_npm_cli_path,
    embedded_npm_path,
    ensure_node_on_path,
    exe_suffix,
    get_servers_dir,
    node_is_acceptable,
    node_version_tuple,
    nodeenv_bin_dir,
    nodeenv_root_dir,
    npm_subprocess_env,
    platform_bin_dir,
    preferred_node_path,
    preferred_npm_command,
    sibling_npm_path,
    user_data_dir,
)
from .manifest import (  # noqa: F401
    acquire_lock,
    build_config,
    has_required_tools,
    installed_version,
    manifest_path,
    needs_install,
    npm_specs_fingerprint,
    read_manifest,
    resolve_config,
    resolve_config_from_path,
    tools_fingerprint,
    write_manifest,
)
from .installers import (  # noqa: F401
    NODEENV_VERSION_STAMP,
    asset_url,
    download_asset,
    embedded_node_is_healthy,
    initialize_nodeenv_globals,
    install_archive_tool,
    install_embedded_node,
    install_native_tools,
    install_node_tools,
    install_package_manager_tools,
    install_tools,
    nodeenv_needs_unofficial_builds,
    package_manager_tool_dir,
)
from .manifest import package_manager_tool_path  # noqa: F401
