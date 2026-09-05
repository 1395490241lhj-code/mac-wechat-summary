"""The shared driver: exit semantics, timeout, fail-closed, cleanup-always."""

from __future__ import annotations

import io
import subprocess

import pytest

from agent_runner import (DigestResult, ShadowError, check_tool_boundary,
                          run_shadow)

FOUR = frozenset({"a", "b", "c", "d"})


class StubRunner:
    backend = "stub"
    expected_tools = FOUR

    def __init__(self, *, boundary=None, digest=None, cleanup=None):
        self.calls: list[str] = []
        self._boundary = boundary or (lambda: set(FOUR))
        self._digest = digest or (lambda: DigestResult("摘要", "run-1", 0))
        self._cleanup = cleanup or (lambda run_id: None)
        self.cleaned_with = "unset"

    def preflight(self):
        self.calls.append("preflight")

    def assert_tool_boundary(self):
        self.calls.append("boundary")
        return self._boundary()

    def digest(self):
        self.calls.append("digest")
        return self._digest()

    def cleanup(self, run_id):
        self.calls.append("cleanup")
        self.cleaned_with = run_id
        self._cleanup(run_id)


def drive(runner):
    out, err = io.StringIO(), io.StringIO()
    status = run_shadow(runner, out=out, err=err, install_signals=False)
    return status, out.getvalue(), err.getvalue()


def test_success_prints_digest_to_stdout_only_and_exits_zero():
    r = StubRunner()
    status, out, err = drive(r)
    assert status == 0
    assert out.strip() == "摘要"
    assert "摘要" not in err
    assert r.calls == ["preflight", "boundary", "digest", "cleanup"]
    assert r.cleaned_with == "run-1"


def test_failed_run_is_one_and_prints_nothing_to_stdout():
    r = StubRunner(digest=lambda: DigestResult("", "run-2", 1))
    status, out, err = drive(r)
    assert status == 1 and out == "" and "no digest" in err
    assert r.calls[-1] == "cleanup" and r.cleaned_with == "run-2"


def test_empty_digest_with_zero_returncode_is_still_a_failed_run():
    status, out, _ = drive(StubRunner(digest=lambda: DigestResult("", None, 0)))
    assert status == 1 and out == ""


def test_boundary_abort_is_two_reads_nothing_and_still_cleans_up():
    def bad():
        raise ShadowError("tool boundary: refusing to run")
    r = StubRunner(boundary=bad)
    status, out, err = drive(r)
    assert status == 2 and out == "" and "ABORTED" in err
    assert "digest" not in r.calls and r.calls[-1] == "cleanup"


def test_timeout_is_a_failed_run_with_cleanup():
    def slow():
        raise subprocess.TimeoutExpired(cmd="agent", timeout=900)
    r = StubRunner(digest=slow)
    status, out, err = drive(r)
    assert status == 1 and out == "" and "timed out" in err
    assert r.calls[-1] == "cleanup"


def test_interrupt_is_130_with_cleanup():
    def interrupted():
        raise KeyboardInterrupt("signal 2")
    r = StubRunner(digest=interrupted)
    status, _, err = drive(r)
    assert status == 130 and "INTERRUPTED" in err and r.calls[-1] == "cleanup"


def test_cleanup_failure_is_three_and_never_masks_a_primary_failure():
    def broken(_run_id):
        raise RuntimeError("residue")
    status, _, err = drive(StubRunner(cleanup=broken))
    assert status == 3 and "CLEANUP FAILED" in err

    def bad():
        raise ShadowError("boundary")
    status, _, _ = drive(StubRunner(boundary=bad, cleanup=broken))
    assert status == 2, "the abort outranks the cleanup failure"


@pytest.mark.parametrize("tools,fragment", [
    (FOUR | {"terminal"}, "MUTATING"),
    (FOUR | {"mcp__other__read"}, "unexpected"),
    (FOUR - {"a"}, "absent"),
])
def test_check_tool_boundary_fails_closed(tools, fragment):
    with pytest.raises(ShadowError, match=fragment):
        check_tool_boundary(set(tools), FOUR)


def test_check_tool_boundary_accepts_exactly_the_expected_set():
    check_tool_boundary(set(FOUR), FOUR)
