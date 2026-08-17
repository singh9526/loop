"""The layer rules, enforced rather than documented.

Each of these encodes a rule from the spec that a well-meaning import
would quietly break.
"""

import ast
import subprocess
import sys
from pathlib import Path

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


# --- AST-based import resolution, shared by the three rules below ---
#
# A text/substring scan for "import loop.core" misses `from loop import
# core`, `from ..core import x`, and `importlib.import_module("loop.core")`
# — and it can also false-positive on a docstring that merely *mentions*
# "loop.core" in prose, which is not an import at all. A scan for the
# literal spelling `Name('jsonl').attr == 'append'` similarly misses
# `from loop.store.jsonl import append as x; x(...)` and the fully
# qualified `loop.store.jsonl.append(...)`. Both gaps were found in
# review by mutation-testing the naive version of this file. Everything
# below resolves an import or a call to the fully qualified dotted name
# it actually refers to, the way the interpreter would, before any rule
# compares against it.


def _package_of(path: Path) -> str:
    """The dotted package a module belongs to — what `__package__` would
    be at runtime, used to resolve relative imports. The same formula
    (drop the last path component) works for both a regular module and
    an `__init__.py`, because an `__init__.py`'s own dotted name already
    *is* the package relative imports inside it resolve against."""
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1])


def _resolve_from(module: str | None, level: int, package: str) -> str:
    """The absolute dotted module named by a `from X import ...` clause's
    `X`, with any leading dots resolved against this file's own package.
    Mirrors `importlib._bootstrap._resolve_name`."""
    if level == 0:
        return module or ""
    bits = package.rsplit(".", level - 1)
    base = bits[0]
    return f"{base}.{module}" if module else base


def _bindings(tree: ast.AST, package: str) -> dict[str, str]:
    """Local name -> the fully qualified thing it actually names, so a
    call site can be resolved the way the interpreter resolves it rather
    than by matching whatever alias the author happened to type."""
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(node.module, node.level, package)
            for alias in node.names:
                if alias.name == "*":
                    continue
                target = f"{base}.{alias.name}" if base else alias.name
                bindings[alias.asname or alias.name] = target
    return bindings


def _dotted(node: ast.expr, bindings: dict[str, str]) -> str | None:
    """The full dotted path an attribute/name expression spells out,
    substituting any aliased import binding for its base name — so
    `jsonl.append`, where `jsonl` was bound by `from loop.store import
    jsonl`, resolves to `loop.store.jsonl.append`, not the literal text
    `jsonl.append`."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(bindings.get(current.id, current.id))
    return ".".join(reversed(parts))


def _import_targets(path: Path) -> list[str]:
    """Every absolute dotted path this file's imports could resolve to:
    what a plain `import`/`from ... import` statement names (both the
    module and, since `from loop import store` must be caught even
    though `loop` alone is not forbidden, the combined module+name too),
    and what a string literal handed to `importlib.import_module` or
    `__import__` names — including a relative (leading-dot) literal."""
    package = _package_of(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bindings = _bindings(tree, package)
    targets: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(node.module, node.level, package)
            targets.append(base)
            targets.extend(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
        elif isinstance(node, ast.Call):
            callee = _dotted(node.func, bindings)
            if callee in ("importlib.import_module", "__import__") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    literal = first.value
                    if literal.startswith("."):
                        level = len(literal) - len(literal.lstrip("."))
                        targets.append(
                            _resolve_from(literal[level:] or None, level, package)
                        )
                    else:
                        targets.append(literal)

    return targets


def _forbidden_hits(path: Path, forbidden: tuple[str, ...]) -> list[str]:
    hits = []
    for target in _import_targets(path):
        for name in forbidden:
            if target == name or target.startswith(f"{name}."):
                hits.append(name)
    return hits


def test_jsonl_append_has_exactly_one_caller():
    """Every write must go through the lock. A second caller is a race —
    under any spelling: `jsonl.append(...)`, the fully qualified
    `loop.store.jsonl.append(...)`, or an aliased
    `from loop.store.jsonl import append as x; x(...)`."""
    target = "loop.store.jsonl.append"
    callers = []
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bindings = _bindings(tree, _package_of(path))
        calls_target = any(
            isinstance(node, ast.Call) and _dotted(node.func, bindings) == target
            for node in ast.walk(tree)
        )
        if calls_target:
            callers.append(path.relative_to(ROOT).as_posix())
    assert callers == ["loop/app/writer.py"]


def test_core_imports_nothing_from_the_outer_layers():
    forbidden = ("loop.store", "loop.blockers", "loop.app", "loop.gui")
    offenders = []
    for path in (PACKAGE / "core").rglob("*.py"):
        for name in _forbidden_hits(path, forbidden):
            offenders.append((path.name, name))
    assert offenders == []


def test_blockers_import_nothing_from_core():
    """The seam that made a Windows overlay one new file. It still holds:
    blockers speak only Prompt and Answers."""
    offenders = []
    for path in (PACKAGE / "blockers").rglob("*.py"):
        if _forbidden_hits(path, ("loop.core",)):
            offenders.append(path.name)
    assert offenders == []


def test_the_daemon_and_the_overlays_are_gone():
    for relative in ("sched", "blockers/macos.py", "blockers/tk.py",
                     "blockers/factory.py"):
        assert not (PACKAGE / relative).exists(), f"{relative} survived"
