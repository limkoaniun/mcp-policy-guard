"""The declared dependencies must match what the code actually imports.

Reproducibility is a stated value of this artifact, so `pyproject.toml` is a
claim about what it takes to run the experiments -- and a claim that resolves
to something the code cannot import is worse than no claim at all. `mcp>=1.0`
was exactly that: the branch needs `MCPServer`, the `Client` facade and
`Tool.input_schema`, none of which exist in mcp 1.x, so a fresh install was
free to produce a tree that fails on import.

The general test below is what stops the specific one from being fixed once
and drifting again: it walks the imports and checks each third-party
distribution is declared, rather than pinning a hand-kept list.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tomllib
from importlib.metadata import packages_distributions

REPO = pathlib.Path(__file__).resolve().parents[1]

# Declared under [project.optional-dependencies].dev, or provided by the
# interpreter, so absent from the runtime dependency list by design.
NOT_RUNTIME_DEPENDENCIES = {"pytest", "ruff"}

# This repo's own packages, and anything importable from the standard library.
LOCAL_PACKAGES = {"enrolment", "policy_guard"}


def normalise(name: str) -> str:
    return name.lower().replace("_", "-")


def metadata() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def declared_distributions() -> set[str]:
    project = metadata()["project"]
    requirements = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        requirements += list(extra)
    return {
        normalise(
            requirement.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
        )
        for requirement in requirements
    }


def imported_top_level_modules() -> set[str]:
    modules = set()
    for root in ("src", "tests"):
        for path in (REPO / root).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules |= {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    modules.add(node.module.split(".")[0])
    return modules


def test_the_mcp_floor_is_the_version_this_code_needs():
    """`MCPServer`, `Client` and `Tool.input_schema` are all 2.x API."""
    requirement = next(
        r for r in metadata()["project"]["dependencies"] if r.startswith("mcp")
    )
    assert requirement.startswith("mcp>=2"), (
        f"declared {requirement!r}; the branch does not run on mcp 1.x"
    )


def test_every_imported_third_party_package_is_declared():
    """Including `mcp_types` and `anyio`, both imported directly and neither
    previously declared -- they happened to be installed as dependencies of
    `mcp`, which is a coincidence of the resolver, not a stated requirement.
    """
    provided_by = packages_distributions()
    undeclared = []
    declared = declared_distributions()
    for module in sorted(imported_top_level_modules()):
        if module in LOCAL_PACKAGES or module in sys.stdlib_module_names:
            continue
        distributions = {normalise(name) for name in provided_by.get(module, [])}
        if not distributions:
            continue  # not installed here; nothing to check against
        if distributions & NOT_RUNTIME_DEPENDENCIES:
            continue
        if not (distributions & declared):
            undeclared.append(f"{module} (from {sorted(distributions)})")
    assert not undeclared, (
        "imported but not declared in pyproject.toml: " + "; ".join(undeclared)
    )
