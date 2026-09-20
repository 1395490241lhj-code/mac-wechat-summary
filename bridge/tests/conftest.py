"""The bridge suite must never read the real user's own directories.

Database mode resolves its recorded decision from the app-support directory
under the current user's home. A test that reaches that path by accident
depends on whatever the developer happens to have there, which is how a suite
passes on one machine and fails on another. This fixture points the derivation
at the test's temporary directory instead, and leaves an explicit home
argument -- which tests pass deliberately -- untouched.
"""

from __future__ import annotations

import pytest

import acquired_database_source


@pytest.fixture(autouse=True)
def _isolated_recorded_source(tmp_path, monkeypatch):
    original = acquired_database_source.recorded_manifest_path
    monkeypatch.setattr(
        acquired_database_source,
        "recorded_manifest_path",
        lambda home=None: original(tmp_path if home is None else home),
    )
