"""The package layout is the worker/host boundary, and imports respect it.

``s3_listing_study.attempt`` runs inside a subject image; ``common`` is what both
sides share and therefore also ships there; ``host`` never does. The Dockerfile
copies the first two subtrees and not the third, so the boundary holds only as
long as nothing on the shipped side imports the unshipped one.

Both failure directions are expensive in their own way. A worker module reaching
into ``host`` produces an ImportError inside a container at attempt time, on a
runner — the latest and most costly place to find out. A ``host`` module drifting
into ``common`` widens what ships next to eleven third-party binaries and moves
every derived image's digest whenever it is edited, invalidating the pins a
campaign runs against.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "src" / "s3_listing_study"
DOCKERFILE = REPO / "harness" / "derived-image" / "Dockerfile"
SHIPPED = ("attempt", "common")


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


def test_shipped_layers_never_import_host() -> None:
    offenders: list[str] = []
    for module, path in sorted(_modules().items()):
        if _layer(module) not in SHIPPED:
            continue
        for imported in sorted(_imported_names(module, path)):
            if _layer(imported) == "host":
                offenders.append(f"{module} -> {imported}")
    assert not offenders, (
        "modules that ship in a subject image import host-only code, which will "
        f"ImportError at attempt time: {offenders}"
    )


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


def test_common_is_actually_shared() -> None:
    """common/ is the intersection, not a drawer.

    Reachability, not direct import: a module both sides reach only through
    another common module is still genuinely shared. Anything one side cannot
    reach at all belongs in that other side's layer — worker-only code in
    common/ ships and pins for no reason, host-only code there is the drift this
    whole boundary exists to prevent.
    """
    modules = _modules()
    from_worker = _reachable_from("attempt")
    from_host = _reachable_from("host")

    misplaced: list[str] = []
    for module in sorted(modules):
        if _layer(module) != "common" or module == "s3_listing_study.common":
            continue
        if module not in from_worker:
            misplaced.append(f"{module} (no attempt/ module reaches it — belongs in host/)")
        elif module not in from_host:
            misplaced.append(f"{module} (no host/ module reaches it — belongs in attempt/)")
    assert not misplaced, f"modules in common/ that are not actually shared: {misplaced}"


def test_dockerfile_ships_exactly_the_shipped_layers() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    for layer in SHIPPED:
        assert f"COPY src/s3_listing_study/{layer}/" in text, (
            f"the derived image must copy s3_listing_study/{layer}/"
        )
    assert "COPY src/s3_listing_study/host/" not in text, (
        "host-only code must not be copied into a subject image"
    )
    assert "COPY src/s3_listing_study/ " not in text, (
        "copying the whole package puts host-only modules back in the image"
    )
