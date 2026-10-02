"""Every third-party package the scheduled runs import is declared in pyproject's base dependencies (review of
1 Oct 2026: pandas, a Parquet engine and yfinance were missing, so a clean install could not start the live path)."""
import ast
import sys
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

import autorun

NAMES = {"pydantic_settings": "pydantic-settings"}  # import name -> package name, where they differ
# imported only inside a step that is not part of a scheduled run, or behind a flag that is off
OPTIONAL = {"neo4j", "streamlit", "altair", "torch", "transformers", "datasets", "llmcompressor", "skfolio", "cvxpy"}


def local(name: str) -> Path | None:
    parts = name.split(".")
    for base in (BACKEND / "scripts" / f"{parts[0]}.py", BACKEND.joinpath(*parts).with_suffix(".py"),
                 BACKEND.joinpath(*parts) / "__init__.py"):
        if base.exists():
            return base
    return None


def third_party(start: list[Path]) -> set[str]:
    seen: set[Path] = set()
    out: set[str] = set()
    todo = list(start)
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for node in ast.walk(ast.parse(f.read_text())):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                    [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
                    if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
            for m in mods:
                hit = local(m)
                if hit:
                    todo.append(hit)
                elif local(m.rsplit(".", 1)[0]) is None and m.split(".")[0] not in sys.stdlib_module_names:
                    out.add(m.split(".")[0])
    return out


def test_the_scheduled_runs_import_only_declared_packages() -> None:
    steps = {c[1] for job in ("events", "allocator", "review", "learn") for m in ("live", "dry")
             for c in autorun.commands(job, m)}
    steps |= {"scripts/autorun.py", "scripts/research_queue.py", "scripts/digest.py", "scripts/desktop_export.py"}
    deps = tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"]["dependencies"]
    declared = {d.split(">")[0].split("=")[0].split("[")[0].strip().lower() for d in deps}
    used = {NAMES.get(m, m).lower() for m in third_party([BACKEND / s for s in steps])} - OPTIONAL
    assert used and used <= declared, f"imported by the scheduled runs but not declared: {sorted(used - declared)}"
    assert {"pyarrow"} <= declared  # pandas reads the Parquet price files through it; never imported by name
