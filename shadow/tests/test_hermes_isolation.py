"""The isolation guard refuses every way a run could reach the real profile.

These tests never set the process's own ``HOME``; the guard takes an explicit
mapping so the suite can exercise refusals without the ability to cause the
damage it is guarding against.
"""

from __future__ import annotations

import pytest

import hermes_isolation as iso


@pytest.fixture
def disposable(tmp_path):
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes_home"
    home.mkdir()
    hermes_home.mkdir()
    return {"HOME": str(home), "HERMES_HOME": str(hermes_home)}


def test_accepts_two_disposable_directories(disposable):
    resolved = iso.enforce_disposable_home(disposable)
    assert set(resolved) == {"HOME", "HERMES_HOME"}
    assert resolved["HOME"] != resolved["HERMES_HOME"]


@pytest.mark.parametrize("name", ["HOME", "HERMES_HOME"])
def test_unset_is_refused_never_defaulted(disposable, name):
    """An absent variable is the incident, not a fallback."""
    del disposable[name]
    with pytest.raises(iso.IsolationRefused, match=f"{name} is not set"):
        iso.enforce_disposable_home(disposable)


@pytest.mark.parametrize("name", ["HOME", "HERMES_HOME"])
def test_empty_is_refused(disposable, name):
    disposable[name] = "   "
    with pytest.raises(iso.IsolationRefused, match=f"{name} is not set"):
        iso.enforce_disposable_home(disposable)


@pytest.mark.parametrize("name", ["HOME", "HERMES_HOME"])
def test_relative_path_is_refused(disposable, name):
    disposable[name] = "relative/dir"
    with pytest.raises(iso.IsolationRefused, match="not an absolute path"):
        iso.enforce_disposable_home(disposable)


@pytest.mark.parametrize("name", ["HOME", "HERMES_HOME"])
def test_missing_directory_is_refused(disposable, tmp_path, name):
    disposable[name] = str(tmp_path / "absent")
    with pytest.raises(iso.IsolationRefused, match="does not exist as a directory"):
        iso.enforce_disposable_home(disposable)


@pytest.mark.parametrize("name", ["HOME", "HERMES_HOME"])
def test_the_account_home_itself_is_refused(disposable, name):
    disposable[name] = str(iso.account_home())
    with pytest.raises(iso.IsolationRefused, match="protected path"):
        iso.enforce_disposable_home(disposable)


@pytest.mark.parametrize("name", ["HOME", "HERMES_HOME"])
def test_the_official_install_is_refused(disposable, name):
    disposable[name] = str(iso.account_home() / ".hermes")
    with pytest.raises(iso.IsolationRefused, match="protected path"):
        iso.enforce_disposable_home(disposable)


@pytest.mark.parametrize("name", ["HOME", "HERMES_HOME"])
def test_anything_under_the_official_install_is_refused(disposable, name):
    disposable[name] = str(iso.account_home() / ".hermes" / "sessions")
    with pytest.raises(iso.IsolationRefused, match="protected path"):
        iso.enforce_disposable_home(disposable)


@pytest.mark.parametrize("name", ["HOME", "HERMES_HOME"])
def test_the_protected_profile_is_refused_by_name_at_any_depth(disposable, tmp_path, name):
    """Even a disposable tree may not be called wechatshadow.

    The profile's bytes are project evidence; a path that merely *looks* like
    it is refused rather than reasoned about.
    """
    decoy = tmp_path / "scratch" / "wechatshadow" / "deep"
    decoy.mkdir(parents=True)
    disposable[name] = str(decoy)
    with pytest.raises(iso.IsolationRefused, match="wechatshadow"):
        iso.enforce_disposable_home(disposable)


def test_protected_paths_do_not_depend_on_the_environment(monkeypatch, tmp_path):
    """``$HOME`` is the variable under test, so it cannot define "real"."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert iso.account_home() != tmp_path
    assert iso.account_home() in iso.protected_paths()


def test_refusal_names_both_required_variables(disposable):
    del disposable["HOME"]
    with pytest.raises(iso.IsolationRefused) as excinfo:
        iso.enforce_disposable_home(disposable)
    assert "HOME and HERMES_HOME" in str(excinfo.value)


def test_guard_module_imports_nothing_from_hermes():
    """It must be safe to call before a Hermes tree is on ``sys.path``."""
    import ast
    from pathlib import Path

    source = Path(iso.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "os", "pwd", "pathlib"}
