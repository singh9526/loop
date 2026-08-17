"""The layer rules, enforced rather than documented.

Each of these encodes a rule from the spec that a well-meaning import
would quietly break.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "loop"

POISON = """
import sys

class Poison:
    def __getattr__(self, name):
        raise AssertionError("PySide6 was imported outside loop/gui/")

for name in ("PySide6", "PySide6.QtCore", "PySide6.QtGui",
             "PySide6.QtWidgets", "PySide6.QtNetwork"):
    sys.modules[name] = Poison()

import importlib
for module in {modules!r}:
    importlib.import_module(module)
print("clean")
"""


def _modules(exclude: str) -> list[str]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if exclude in relative.parts:
            continue
        parts = relative.with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            found.append(".".join(parts))
    return found


def test_nothing_outside_gui_imports_pyside6():
    """A single convenience import in app/ or core/ would make the CLI
    unusable without a 300MB GUI toolkit."""
    program = POISON.format(modules=_modules(exclude="gui"))
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, cwd=ROOT
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def _calls_to(path: Path, dotted: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = dotted.split(".")[-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == target:
            if isinstance(node.value, ast.Name) and node.value.id == dotted.split(".")[0]:
                return True
    return False


def test_jsonl_append_has_exactly_one_caller():
    """Every write must go through the lock. A second caller is a race."""
    callers = [
        path.relative_to(ROOT).as_posix()
        for path in PACKAGE.rglob("*.py")
        if _calls_to(path, "jsonl.append")
    ]
    assert callers == ["loop/app/writer.py"]


def test_core_imports_nothing_from_the_outer_layers():
    forbidden = ("loop.store", "loop.blockers", "loop.app", "loop.gui")
    offenders = []
    for path in (PACKAGE / "core").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            if f"import {name}" in source or f"from {name}" in source:
                offenders.append((path.name, name))
    assert offenders == []


def test_blockers_import_nothing_from_core():
    """The seam that made a Windows overlay one new file. It still holds:
    blockers speak only Prompt and Answers."""
    offenders = []
    for path in (PACKAGE / "blockers").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "loop.core" in source:
            offenders.append(path.name)
    assert offenders == []


def test_the_daemon_and_the_overlays_are_gone():
    for relative in ("sched", "blockers/macos.py", "blockers/tk.py",
                     "blockers/factory.py"):
        assert not (PACKAGE / relative).exists(), f"{relative} survived"
