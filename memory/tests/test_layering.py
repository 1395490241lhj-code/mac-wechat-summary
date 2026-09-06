"""M1 is an added layer, and the dependency runs one way only.

These are cheap source-level assertions, but they are the ones that stop the
memory layer becoming load-bearing before anyone has decided it should be.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXISTING = ("bridge", "shadow")


def python_files(directory: Path):
    return [
        path
        for path in (ROOT / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    ]


@pytest.mark.parametrize("directory", EXISTING)
def test_no_existing_module_imports_the_memory_layer(directory):
    """Nothing in the shipped path depends on M1 -- it is additive only."""
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in python_files(Path(directory))
        if any(
            name in path.read_text(encoding="utf-8")
            for name in ("memory_store", "memory_ingest", "memory_retrieval",
                         "memory_query", "memory_sync", "memory_consent")
        )
    ]
    assert offenders == []


def test_the_memory_layer_does_not_redefine_the_normalised_shape():
    """It imports the reader boundary; a second copy would fork the model."""
    for path in python_files(Path("memory")):
        text = path.read_text(encoding="utf-8")
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        assert "class NormalizedMessage" not in text
        assert "class NormalizedConversation" not in text


def imported_modules(path: Path) -> set[str]:
    """Top-level module names a file imports, wherever the import sits.

    Read from the syntax tree rather than the text, so prose about what the
    layer does not do cannot pass or fail a structural test.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


#: Everything the memory layer is allowed to depend on. Standard library, the
#: reader boundary, and itself. Anything else is a new dependency and should
#: have to be argued for in review rather than appear.
ALLOWED_IMPORTS = {
    "__future__", "argparse", "ast", "dataclasses", "hashlib", "os", "pathlib",
    "plistlib", "secrets", "sqlite3", "subprocess", "sys", "time", "typing",
    "unicodedata",
    "memory_consent", "memory_identity", "memory_ingest", "memory_query",
    "memory_retrieval", "memory_store", "memory_sync", "message_source",
    # The bridge's own reader, imported lazily by the explicit sync CLI only.
    "wechat_companion_mcp",
}


def implementation_files():
    return [
        path
        for path in python_files(Path("memory"))
        if not path.name.startswith("test_") and path.name != "conftest.py"
    ]


def test_the_memory_layer_imports_nothing_new():
    """No provider SDK, no HTTP client, no vector or embedding library."""
    for path in implementation_files():
        assert imported_modules(path) <= ALLOWED_IMPORTS, path.name


def test_no_mcp_server_is_imported_or_declared_in_m1():
    """The public memory surface is an M2 decision, not an M1 side effect."""
    for path in implementation_files():
        assert "mcp" not in imported_modules(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    assert "tool" not in ast.dump(decorator)
