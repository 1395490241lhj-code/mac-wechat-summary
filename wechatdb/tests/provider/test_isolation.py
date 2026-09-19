"""The provider stays isolated: the leaf is free of it, and it is free of product.

Placed here, beside the thing it guards, and deliberately not under
``bridge/tests``: the Memory layering test scans every file under ``bridge/``
as raw text for memory-module names, and a guard that spelled those names as
forbidden literals would fail that test on the strength of its own source.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROVIDER = ROOT / "wechatdb" / "provider"


def imported_roots(tree: ast.AST) -> set[str]:
    """First dotted segment of every absolute import, anywhere in the module."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def provider_modules() -> list[Path]:
    return sorted(p for p in PROVIDER.rglob("*.py") if "__pycache__" not in p.parts)


def docstring_nodes(tree: ast.AST) -> set[int]:
    """The docstring constants, so prose about what is absent is not scanned."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def test_importing_the_leaf_parser_does_not_import_the_provider():
    """A fresh interpreter, ``import wechatdb``, and the provider is absent.

    A subprocess rather than this process, whose ``sys.modules`` may already
    carry the provider from an earlier test. Nothing is deleted afterwards to
    make the assertion true: the import itself must not bring it in.
    """
    probe = (
        "import sys\n"
        "assert not any(m == 'wechatdb.provider' or m.startswith('wechatdb.provider.')"
        " for m in sys.modules)\n"
        "import wechatdb\n"
        "print('wechatdb.provider' in sys.modules,"
        " any(m.startswith('wechatdb.provider') for m in sys.modules))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}, check=True,
    )
    assert completed.stdout.strip() == "False False"

    # And the export surface itself: the top-level package names no provider.
    tree = ast.parse((ROOT / "wechatdb" / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "provider" not in (node.module or ""), ast.unparse(node)
            assert all("provider" not in a.name for a in node.names), ast.unparse(node)
        elif isinstance(node, ast.Import):
            assert all("provider" not in a.name for a in node.names), ast.unparse(node)


def test_the_isolated_provider_imports_no_product_layer():
    """Dependency direction, read from the syntax tree, matched by segment.

    Prefix on the first segment, so the guard never has to spell a full
    product module name in its own source.
    """
    offenders: list[str] = []
    for path in provider_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for root in sorted(imported_roots(tree)):
            if root.startswith("memory") or root in {"shadow", "ai", "core"}:
                offenders.append(f"{path.relative_to(ROOT)}: {root}")
    assert offenders == [], offenders


FORBIDDEN_MODULES = {"subprocess", "glob", "ctypes", "lldb", "shutil"}
FORBIDDEN_CALLS = {"glob", "iglob", "rglob", "walk", "listdir", "scandir",
                   "iterdir", "Popen", "system", "spawn"}
FORBIDDEN_TEXT = ("PRAGMA key", "sqlcipher", "immutable", "/Users/", "Containers",
                  "com.tencent", "xwechat_files", "db_storage", "codesign", "sudo")


def test_the_isolated_provider_has_no_acquisition_capability():
    """It can be handed a database. It cannot find, open or unlock one.

    No filesystem search, no default root, no key handling, no decryption, no
    process access, no shelling out, and no ``immutable`` open, which would
    silently drop an unread write-ahead log.
    """
    offenders: list[str] = []
    for path in provider_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(ROOT)
        prose = docstring_nodes(tree)
        for root in sorted(imported_roots(tree) & FORBIDDEN_MODULES):
            offenders.append(f"{rel}: import {root}")
        for node in ast.walk(tree):
            if id(node) in prose:
                continue
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_CALLS:
                offenders.append(f"{rel}: .{node.attr}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
                offenders.append(f"{rel}: {node.id}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                for marker in FORBIDDEN_TEXT:
                    if marker.lower() in node.value.lower():
                        offenders.append(f"{rel}: {marker!r}")
    assert offenders == [], offenders
