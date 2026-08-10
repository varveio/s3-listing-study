"""Read the immutable distro-runtime identity baked into the shared image."""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Final

RUNTIME_IDENTITY_PATH: Final = Path("/opt/s3-listing-study/runtime-identity.json")


def interpreter_identity(path: Path = RUNTIME_IDENTITY_PATH) -> dict[str, str | None]:
    """Return the image marker, or an explicit local-development identity."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        libc_name, libc_version = platform.libc_ver()
        return {
            "architecture": platform.machine(),
            "implementation": platform.python_implementation(),
            "libc": f"{libc_name}-{libc_version}" if libc_name else None,
            "package_manifest_sha256": None,
            "running_version": platform.python_version(),
            "source": "local-development",
        }
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"runtime identity marker is malformed: {exc}") from exc
    fields = {
        "architecture",
        "implementation",
        "libc",
        "package_manifest_sha256",
        "running_version",
        "source",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RuntimeError("runtime identity marker has an unexpected field set")
    if any(item is not None and not isinstance(item, str) for item in value.values()):
        raise RuntimeError("runtime identity marker fields must be strings or null")
    return value
