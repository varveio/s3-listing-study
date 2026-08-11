"""The package layout is the worker/manager boundary, and imports respect it.

``s3_listing_study.worker`` is what a Cloud Batch attempt task executes;
``common`` is what both roles share and therefore ships alongside it;
``manager`` is the orchestrating side and never enters an image. The Dockerfile
copies the first two subtrees and not the third, so the boundary holds only as
long as nothing on the shipped side imports the unshipped one.

The expensive failure direction is a shipped worker/common module reaching into
``manager``: it produces an ImportError inside a container at attempt time, on a
runner — the latest and most costly place to find out. Manager imports of
``common`` are intentional; that is the shared layer's purpose. The reachability
test below keeps every common module genuinely shared so manager-only code does
not drift into final assembly and change every final per-tool image's digest.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "src" / "s3_listing_study"
DOCKERFILE = REPO / "harness" / "derived-image" / "Dockerfile"

WORKER = "worker"
REPO_LAYER = "repo"
MANAGER = "manager"
COMMON = "common"
SHIPPED = (WORKER, COMMON)


def _modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(PACKAGE.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    return modules


def _resolve(current: str, node: ast.ImportFrom, is_package_init: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = current.split(".")
    if not is_package_init:
        base = base[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _imported_names(module: str, path: Path) -> set[str]:
    found: set[str] = set()
    is_package_init = path.name == "__init__.py"
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            base = _resolve(module, node, is_package_init)
            if base.startswith("s3_listing_study"):
                found.add(base)
        elif isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("s3_listing_study")}
    return found


def _layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else "root"


def _reachable_from(layer: str) -> set[str]:
    """Transitive closure of imports starting at every module in one layer."""
    modules = _modules()
    edges = {m: _imported_names(m, p) & set(modules) for m, p in modules.items()}
    seen: set[str] = set()
    stack = [m for m in modules if _layer(m) == layer]
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        stack.extend(edges.get(module, set()))
    return seen


def test_shipped_layers_never_import_manager() -> None:
    offenders: list[str] = []
    for module, path in sorted(_modules().items()):
        if _layer(module) not in SHIPPED:
            continue
        for imported in sorted(_imported_names(module, path)):
            if _layer(imported) in (MANAGER, REPO_LAYER):
                offenders.append(f"{module} -> {imported}")
    assert not offenders, (
        "modules that ship in a final per-tool image import unshipped code, which will "
        f"ImportError at attempt time: {offenders}"
    )


def test_common_is_actually_shared() -> None:
    """common/ is the intersection, not a drawer.

    Reachability, not direct import: a module both roles reach only through
    another common module is still genuinely shared. Anything one role cannot
    reach at all belongs in that other role's layer — worker-only code in
    common/ ships and pins for no reason, manager-only code there is the drift
    this whole boundary exists to prevent.
    """
    modules = _modules()
    from_worker = _reachable_from(WORKER)
    from_manager = _reachable_from(MANAGER)

    misplaced: list[str] = []
    for module in sorted(modules):
        if _layer(module) != COMMON or module == f"s3_listing_study.{COMMON}":
            continue
        if module not in from_worker:
            misplaced.append(f"{module} (no worker/ module reaches it — belongs in manager/)")
        elif module not in from_manager:
            misplaced.append(f"{module} (no manager/ module reaches it — belongs in worker/)")
    assert not misplaced, f"modules in common/ that are not actually shared: {misplaced}"


def test_dockerfile_ships_exactly_the_shipped_layers() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    for layer in SHIPPED:
        assert f"COPY src/s3_listing_study/{layer}/" in text, (
            f"the final per-tool image must copy s3_listing_study/{layer}/"
        )
    for layer in (MANAGER, REPO_LAYER):
        assert f"COPY src/s3_listing_study/{layer}/" not in text, (
            f"{layer}-only code must not be copied into a final per-tool image"
        )
    assert "COPY src/s3_listing_study/ " not in text, (
        "copying the whole package puts manager-only modules back in the image"
    )
