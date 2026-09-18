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


MEMORY_MODULES = ("memory_store", "memory_ingest", "memory_retrieval", "memory_query",
                  "memory_sync", "memory_consent", "memory_freshness", "wechat_memory_mcp")


def test_the_bridge_never_references_the_memory_layer():
    """The raw-message bridge and the memory server share nothing (M2.1)."""
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in python_files(Path("bridge"))
        if any(name in path.read_text(encoding="utf-8") for name in MEMORY_MODULES)
    ]
    assert offenders == []


def test_nothing_in_shadow_imports_the_memory_layer():
    """The shadow runner may *load* the consent gate by path, before a run, to
    refuse a memory request it cannot honour -- and nothing in shadow/ may
    import memory. The runner keeps no import-time dependency on what it
    launches; the CLI merely names the server script in its help text."""
    for path in python_files(Path("shadow")):
        if "tests" in path.parts:
            continue
        assert imported_modules(path).isdisjoint(MEMORY_MODULES), path.name
    loaders = [
        path.relative_to(ROOT).as_posix()
        for path in python_files(Path("shadow"))
        if "tests" not in path.parts and "spec_from_file_location" in path.read_text(encoding="utf-8")
    ]
    assert loaders == ["shadow/runners/claude.py"]


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
    "__future__", "argparse", "ast", "dataclasses", "hashlib", "json", "os", "pathlib",
    "plistlib", "secrets", "sqlite3", "subprocess", "sys", "time", "typing",
    "unicodedata", "urllib",
    # The MCP SDK, used by the read-only memory server only (M2.1).
    "mcp",
    "memory_consent", "memory_freshness", "memory_identity", "memory_ingest", "memory_paths",
    "memory_query", "memory_retrieval", "memory_store", "memory_sync", "memory_worker",
    "message_source",
    # The bridge's own source selection, imported lazily by the sync path only.
    # Since M2.2d this is ``store_access``, which carries no MCP SDK, so the
    # bundled worker can use the bridge's rule without the server's
    # dependencies. The memory MCP server must still never import it.
    "store_access",
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


def test_the_mcp_sdk_is_confined_to_the_memory_server_module():
    """One file speaks MCP; the store, ingestor, query and sync never do."""
    for path in implementation_files():
        if path.name == "wechat_memory_mcp.py":
            assert "mcp" in imported_modules(path)
            continue
        assert "mcp" not in imported_modules(path), path.name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    assert "tool" not in ast.dump(decorator)


def test_the_memory_server_never_imports_the_bridge():
    """Two servers, two processes, no shared code path (M2.1)."""
    path = ROOT / "memory" / "wechat_memory_mcp.py"
    for forbidden in ("wechat_companion_mcp", "store_access", "rion_reader_adapter",
                      "message_source"):
        assert forbidden not in imported_modules(path), forbidden


#: The vocabulary the Reader boundary owns. Memory may narrow it for its own
#: schema, but it may not restate it.
COVERAGE_NAMES = ("COVERAGE_COMPLETE", "COVERAGE_PARTIAL",
                  "COVERAGE_UNAVAILABLE", "COVERAGE_NOT_OBSERVED")


def test_the_memory_layer_does_not_redefine_the_coverage_vocabulary():
    """One owner for the four words, and the memory layer is not it.

    Checked structurally and then by identity. Equality would pass on the day
    someone pasted the strings back in, which is the failure being prevented:
    two equal copies are still two copies, and only one of them gets updated
    when a fifth state is added.
    """
    import memory_store
    import message_source

    path = ROOT / "memory" / "memory_store.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    literal_definitions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(
                node.value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in COVERAGE_NAMES:
                literal_definitions.append(target.id)

    assert literal_definitions == [], literal_definitions

    for name in COVERAGE_NAMES:
        assert getattr(memory_store, name) is getattr(message_source, name), name


def test_the_store_still_owns_which_states_it_persists():
    """`COVERAGE_STATES` is a statement about the schema, not the vocabulary.

    It is the narrower subset a stored row may carry, so it stays here even
    though the words it is built from are the boundary's.
    """
    import memory_store
    import message_source

    assert memory_store.COVERAGE_STATES == {
        message_source.COVERAGE_COMPLETE,
        message_source.COVERAGE_PARTIAL,
        message_source.COVERAGE_UNAVAILABLE,
    }
    assert message_source.COVERAGE_NOT_OBSERVED not in memory_store.COVERAGE_STATES
    assert not hasattr(message_source, "COVERAGE_STATES")
