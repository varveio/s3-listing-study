"""Study-specific, upstream-shaped Google Batch executor adaptation."""

from snakemake_executor_plugin_googlebatch import ExecutorSettings as UpstreamSettings
from snakemake_interface_executor_plugins.settings import CommonSettings

from .executor import (
    RUNTIME_PATH,
    build_create_job_request,
)
from .executor import (
    StudyGoogleBatchExecutor as Executor,
)

ExecutorSettings = UpstreamSettings


common_settings = CommonSettings(
    pass_envvar_declarations_to_cmd=True,
    non_local_exec=True,
    implies_no_shared_fs=True,
    job_deploy_sources=True,
    pass_default_storage_provider_args=True,
    pass_default_resources_args=True,
    # The helper image already contains the pinned GCS provider. Enabling this
    # would make every Batch task download plugins with pip.
    auto_deploy_default_storage_provider=False,
)


__all__ = [
    "RUNTIME_PATH",
    "Executor",
    "ExecutorSettings",
    "build_create_job_request",
    "common_settings",
]
