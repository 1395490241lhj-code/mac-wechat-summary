#!/usr/bin/env python3
"""H5-preflight validation of an upstream Hermes tree — synthetic only.

One runner for the whole Hermes provider-wire acceptance. It never reads real
WeChat data, never opens the operator's store, and never writes to the official
Hermes install or the ``wechatshadow`` profile: every Hermes process it starts,
and the single module that imports a Hermes tree, run under an explicit
disposable ``HOME`` and ``HERMES_HOME`` enforced by ``hermes_isolation``.

This module itself imports **nothing** from Hermes. The only importer is
``hermes_probe.py``, which is launched as a subprocess and calls the guard
before the tree reaches its ``sys.path``.

Phases
------
``prepare``  fresh disposable checkout of upstream ``main``; the current heads
             of the two open TUI fixes composed onto it. Their test files
             collide (both create the same new file); a collision confined to
             test files is resolved by keeping both sides, and a collision in
             any other file is a refusal, because that would be a semantic
             conflict rather than two additions.
``offline``  everything provable without an inference credential.
``online``   the credential-dependent acceptance: three behavioural gates, the
             exact-8 wire assertion, the synthetic digest, and the residue
             audit.

Usage
-----
    python3 shadow/hermes_validation.py --work <dir> --phase prepare
    python3 shadow/hermes_validation.py --work <dir> --phase offline
    ANTHROPIC_API_KEY=… python3 shadow/hermes_validation.py --work <dir> --phase online
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from hermes_isolation import PROTECTED_PROFILE_NAME, account_home  # noqa: E402

UPSTREAM_URL = "https://github.com/NousResearch/hermes-agent.git"

#: The two open fixes the Desktop boundary depends on. Names are for reports.
PULL_REQUESTS = {
    89550: "resolve per-turn toolsets for the session's platform",
    88865: "honor agent.disabled_toolsets on desktop/TUI sessions",
}

#: The toolset row the H4.7 configuration must resolve to on `desktop`.
EXPECTED_DESKTOP_TOOLSETS = ["wechat_companion"]

#: H4.7's required Desktop configuration. All of it is required together:
#: the desktop platform row, the deferral switch off (so the surface is an
#: explicit list rather than a dynamic dispatcher, which is not assertable),
#: and the disabled-toolset list.
REQUIRED_CONFIG = """\
platform_toolsets:
  desktop: [wechat_companion]

tools:
  tool_search:
    enabled: off

agent:
  disabled_toolsets:
    - project
    - desktop_ui
    - skills
    - terminal
    - file
    - browser
    - code_execution
    - computer_use
    - delegation
    - cronjob
    - memory
    - web
    - vision
    - todo
    - session_search
    - image_gen
    - bfl
    - tts
    - clarify
"""

#: Where the digest skill lives. Current upstream resolves `-s <name>` from
#: `HERMES_HOME/skills` plus `skills.external_dirs` only -- it no longer
#: discovers `<cwd>/.hermes/skills`, so a run started from the repo reports
#: "Unknown skill(s): wechat-digest" and exits before it resolves any tools.
#: Pointing at the repo directory rather than copying keeps the skill bytes
#: canonical: `SKILL.md` byte-identity is project evidence.
SKILLS_BLOCK = """
skills:
  external_dirs:
    - "{skills_dir}"
"""

MODEL_BLOCK = """
model:
  default: "{model}"
  provider: "anthropic"
  base_url: "{base_url}"
"""

#: Anthropic Messages format is what the exact-8 verifier can read: it takes
#: tool names from ``tools: [{{"name": ...}}]``. An OpenAI Chat-Completions
#: provider nests them under ``function``, every request then reads as
#: not-tools-bearing, and the assertion silently never fires.
DEFAULT_MODEL = "anthropic/claude-sonnet-5"

#: The read-only bridge, as Hermes must see it. Without this block the
#: ``wechat_companion`` toolset name resolves to nothing and the run has no
#: tools to assert. Both bridge opt-ins live here, in the MCP child's own
#: environment, because that is the process that reads the store.
MCP_SERVER_BLOCK = """
mcp_servers:
  wechat_companion:
    command: "{python}"
    args: ["{bridge}"]
    env:
      WECHAT_COMPANION_ALLOW_AGENT_READ: "1"
      WECHAT_COMPANION_DB_PATH: "{db}"
"""

EXPECTED_DISABLED_COUNT = 19


class ValidationError(RuntimeError):
    """A phase could not proceed. Never a soft failure."""


# --- process helpers ---------------------------------------------------------

def run(argv, *, cwd=None, env=None, timeout=1800, check=True):
    proc = subprocess.run([str(a) for a in argv], cwd=cwd and str(cwd), env=env,
                          capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise ValidationError(
            f"command failed ({proc.returncode}): {' '.join(str(a) for a in argv)}\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc


def git(work_tree, *args, **kwargs):
    return run(["git", "-C", str(work_tree), *args], **kwargs)


# --- containment snapshot ----------------------------------------------------

def protected_snapshot() -> dict:
    """Existence, size, mtime and inode of everything that must not change.

    Taken around a whole phase. Inode is included so a delete-and-rewrite is
    caught as well as an in-place edit.
    """
    home = account_home()
    targets = [home / ".hermes" / "SOUL.md", home / ".hermes" / "config.yaml"]
    profile = home / ".hermes" / "profiles" / PROTECTED_PROFILE_NAME
    if profile.is_dir():
        targets += sorted(p for p in profile.rglob("*") if p.is_file())
    snapshot = {}
    for path in targets:
        try:
            st = path.stat()
            snapshot[str(path)] = [st.st_size, st.st_mtime_ns, st.st_ino]
        except OSError:
            snapshot[str(path)] = None
    return snapshot


def diff_snapshots(before: dict, after: dict) -> list[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


# --- disposable environment --------------------------------------------------

def disposable_env(work: Path, name: str, extra: dict | None = None) -> dict:
    """A minimal environment with an isolated HOME and HERMES_HOME.

    Built from scratch: nothing from the operator's shell reaches a Hermes
    process, so no inherited base URL, token or profile can change a result.
    """
    home = work / "iso" / name / "home"
    hermes_home = work / "iso" / name / "hermes_home"
    tmp = work / "iso" / name / "tmp"
    for d in (home, hermes_home, tmp):
        d.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(home), "HERMES_HOME": str(hermes_home), "TMPDIR": str(tmp),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "TERM": "dumb",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra or {})
    return env


def write_required_config(env: dict, *, python: Path | None = None,
                          db: Path | None = None, base_url: str | None = None,
                          model: str = DEFAULT_MODEL) -> Path:
    """The H4.7 configuration, plus the bridge and provider when supplied.

    A probe that only resolves toolset names needs neither; a run that must put
    tools on the wire needs the bridge, pointed at a synthetic store, and the
    provider pinned **in the file**. Pinning matters: ``ANTHROPIC_BASE_URL``
    alone was observed to lose to a session's persisted runtime on a second
    run against the same profile, which would send a real request past the
    assertion proxy straight to the provider.
    """
    text = REQUIRED_CONFIG + SKILLS_BLOCK.format(
        skills_dir=REPO / ".hermes" / "skills")
    if base_url is not None:
        text += MODEL_BLOCK.format(model=model, base_url=base_url)
    if python is not None and db is not None:
        text += MCP_SERVER_BLOCK.format(
            python=python, bridge=REPO / "bridge" / "wechat_companion_mcp.py", db=db)
    path = Path(env["HERMES_HOME"]) / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- prepare -----------------------------------------------------------------

def phase_prepare(work: Path, report: dict) -> None:
    upstream = work / "upstream"
    if not (upstream / ".git").is_dir():
        upstream.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--filter=blob:none", "--no-checkout", UPSTREAM_URL, str(upstream)],
            timeout=3600)
    git(upstream, "fetch", "--prune", "origin", "main", timeout=3600)
    refspecs = [f"refs/pull/{n}/head:pr{n}" for n in PULL_REQUESTS]
    git(upstream, "fetch", "-f", "origin", *refspecs, timeout=3600)

    main_oid = git(upstream, "rev-parse", "origin/main").stdout.strip()
    heads = {n: git(upstream, "rev-parse", f"pr{n}").stdout.strip() for n in PULL_REQUESTS}

    base = work / "base"
    composed = work / "composed"
    for tree in (base, composed):
        if tree.exists():
            git(upstream, "worktree", "remove", "--force", str(tree), check=False)
            shutil.rmtree(tree, ignore_errors=True)
    git(upstream, "worktree", "prune")
    git(upstream, "worktree", "add", "--detach", str(base), main_oid)
    git(upstream, "worktree", "add", "--detach", str(composed), main_oid)

    merges = {}
    for number in PULL_REQUESTS:
        proc = git(composed, "-c", "user.email=validation@local", "-c", "user.name=validation",
                   "merge", "--no-ff", f"pr{number}", "-m", f"compose #{number}", check=False)
        conflicts = [p for p in git(composed, "diff", "--name-only", "--diff-filter=U"
                                    ).stdout.split() if p]
        if proc.returncode != 0:
            non_test = [p for p in conflicts if not _is_test_path(p)]
            if non_test:
                git(composed, "merge", "--abort", check=False)
                raise ValidationError(
                    f"#{number} conflicts outside test files: {non_test}. That is a semantic "
                    "conflict between the two fixes and must be resolved deliberately, "
                    "not by this runner.")
            if not conflicts:
                git(composed, "merge", "--abort", check=False)
                raise ValidationError(f"#{number} failed to merge with no conflicted paths")
            for path in conflicts:
                _keep_both_sides(composed / path)
                git(composed, "add", path)
            git(composed, "-c", "user.email=validation@local", "-c", "user.name=validation",
                "commit", "-m", f"compose #{number} (tests: both sides kept)")
        merges[number] = {"conflicts": conflicts, "clean": proc.returncode == 0}

    report["prepare"] = {
        "upstream_main": main_oid,
        "pull_request_heads": heads,
        "pull_request_titles": PULL_REQUESTS,
        "merges": merges,
        "composed_head": git(composed, "rev-parse", "HEAD").stdout.strip(),
        "server_diff_stat": git(composed, "diff", "--stat", main_oid, "--",
                                "tui_gateway/server.py").stdout.strip(),
        "conflict_markers_left": _count_markers(composed),
    }
    if report["prepare"]["conflict_markers_left"]:
        raise ValidationError("conflict markers remain in the composed tree")


def _is_test_path(path: str) -> bool:
    parts = Path(path).parts
    return "tests" in parts or Path(path).name.startswith("test_")


_CONFLICT = re.compile(
    r"^<{7}[^\n]*\n(?P<ours>.*?)^={7}\n(?P<theirs>.*?)^>{7}[^\n]*\n",
    re.MULTILINE | re.DOTALL)


def _keep_both_sides(path: Path) -> None:
    """Resolve an add/add collision by concatenating both sides.

    Only correct when the two sides are independent additions to one file --
    which is why the caller refuses to reach here for anything but a test file.
    """
    text = path.read_text(encoding="utf-8")
    path.write_text(_CONFLICT.sub(lambda m: m.group("ours") + m.group("theirs"), text),
                    encoding="utf-8")


def _count_markers(tree: Path) -> int:
    """Files still carrying a real conflict marker.

    Only the labelled ``<<<<<<< ``/``>>>>>>> `` forms are searched: a bare
    ``=======`` line is ordinary Markdown heading underline and matching it
    reports most documentation trees as conflicted. Unmerged index entries are
    counted too, since a marker-free file can still be staged unresolved.
    """
    proc = run(["git", "-C", str(tree), "grep", "-lE", r"^(<{7}|>{7}) "], check=False)
    files = [line for line in proc.stdout.splitlines() if line.strip()]
    unmerged = [l for l in git(tree, "ls-files", "-u").stdout.splitlines() if l.strip()]
    return len(files) + len(unmerged)


# --- offline -----------------------------------------------------------------

def probe(work: Path, tree: Path, name: str, *, desktop: bool,
          venv_python: Path) -> dict:
    """Resolve one tree's toolsets in a child process, under the guard.

    The interpreter must be the one carrying Hermes's dependencies: the probe
    imports the tree, and this runner's own interpreter deliberately cannot.
    """
    extra = {"HERMES_DESKTOP": "1"} if desktop else {}
    env = disposable_env(work, name, extra)
    write_required_config(env)
    out = work / "iso" / name / "probe.json"
    proc = run([venv_python, HERE / "hermes_probe.py", "--tree", tree, "--out", out,
                "--platform", "desktop", "--platform", "cli"],
               env=env, timeout=600, check=False)
    # The runtime prints its own warnings to stdout ("platform 'desktop' has no
    # valid toolsets configured…"), so the report travels by file, not by pipe.
    if proc.returncode != 0 or not out.is_file():
        raise ValidationError(
            f"probe {name} failed ({proc.returncode}): {proc.stderr[-1200:]} {proc.stdout[-600:]}")
    return json.loads(out.read_text(encoding="utf-8"))


def phase_offline(work: Path, report: dict, venv_python: Path) -> None:
    before = protected_snapshot()
    composed, base = work / "composed", work / "base"
    results: dict = {}

    # O1 -- the guard refuses every protected path, proven here rather than
    # asserted. Its unit tests run in O6; this is the runner's own check that
    # the module it depends on is the one it thinks it is.
    results["O1_guard"] = _guard_selftest()

    # O2 -- composition integrity.
    results["O2_composition"] = {
        "server_py_auto_merged": "tui_gateway/server.py" not in json.dumps(
            report["prepare"]["merges"]),
        "conflict_markers_left": report["prepare"]["conflict_markers_left"],
        "both_semantics_present": _both_semantics_present(composed),
    }

    # O3 -- upstream unit tests, composed vs a same-environment control. The
    # absolute pass count is meaningless in a minimal venv; equality of the
    # failure SETS is what proves the fixes regress nothing.
    results["O3_upstream_tests"] = _upstream_tests(work, composed, base, venv_python)

    # O4 -- the platform gate: the whole Desktop boundary in one resolution.
    composed_desktop = probe(work, composed, "composed_desktop", desktop=True,
                             venv_python=venv_python)
    composed_tui = probe(work, composed, "composed_tui", desktop=False,
                         venv_python=venv_python)
    base_desktop = probe(work, base, "base_desktop", desktop=True,
                         venv_python=venv_python)
    results["O4_platform"] = {
        "composed_desktop": composed_desktop,
        "composed_tui_control": {"session_platform": composed_tui["session_platform"]},
        "base_desktop_control": base_desktop,
        "pass": (
            composed_desktop["session_platform"] == "desktop"
            and composed_desktop["toolsets"]["<session>"] == EXPECTED_DESKTOP_TOOLSETS
            and composed_desktop["toolsets"]["desktop"] == EXPECTED_DESKTOP_TOOLSETS
            and len(composed_desktop["disabled_toolsets"] or []) == EXPECTED_DISABLED_COUNT
            and composed_tui["session_platform"] == "tui"
            # The control must still be broken, or the fixes are not what is
            # being measured.
            and base_desktop["toolsets"]["desktop"] != EXPECTED_DESKTOP_TOOLSETS
        ),
    }

    # O5 -- what the bridge actually puts on the wire, derived not assumed.
    results["O5_wire_surface"] = _wire_surface(work, venv_python)

    # O6 -- this repository's own suites.
    results["O6_repo_tests"] = _repo_tests(work, venv_python)

    # O7 -- the exact-8 assertion itself, on the real wire, with no credential.
    # The proxy's probe mode records a tools-bearing request and rejects it
    # without forwarding, so the boundary is measured before any content could
    # leave the machine. This is the same assertion the online phase makes in
    # enforce mode; running it here means the acceptance does not depend on a
    # provider call to discover a harness fault.
    results["O7_exact8_probe"] = _exact8_probe(work, venv_python)

    results["containment"] = {"changed": diff_snapshots(before, protected_snapshot())}
    results["pass"] = (
        results["O1_guard"]["pass"] and results["O2_composition"]["both_semantics_present"]
        and results["O2_composition"]["conflict_markers_left"] == 0
        and results["O3_upstream_tests"]["pass"] and results["O4_platform"]["pass"]
        and results["O5_wire_surface"]["pass"] and results["O6_repo_tests"]["pass"]
        and results["O7_exact8_probe"]["pass"]
        and not results["containment"]["changed"])
    report["offline"] = results


def _guard_selftest() -> dict:
    import hermes_isolation as iso
    home = account_home()
    cases = {
        "unset": {},
        "account_home": {"HOME": str(home), "HERMES_HOME": str(home)},
        "official_install": {"HOME": "/tmp", "HERMES_HOME": str(home / ".hermes")},
        "protected_profile": {
            "HOME": "/tmp",
            "HERMES_HOME": str(home / ".hermes" / "profiles" / PROTECTED_PROFILE_NAME)},
    }
    refused = {}
    for name, env in cases.items():
        try:
            iso.enforce_disposable_home(env)
            refused[name] = False
        except iso.IsolationRefused:
            refused[name] = True
    return {"refused": refused, "pass": all(refused.values())}


def _both_semantics_present(composed: Path) -> bool:
    source = (composed / "tui_gateway" / "server.py").read_text(encoding="utf-8")
    return ("_disabled_agent_toolsets" in source            # #88865
            and "resolve_platform" in source                # #89550
            and '_get_platform_tools(cfg, "cli"' not in source)


def _upstream_tests(work: Path, composed: Path, base: Path, venv_python: Path) -> dict:
    target = "tests/test_tui_gateway_server.py"
    out = {}
    for label, tree in (("composed", composed), ("base", base)):
        env = disposable_env(work, f"pytest_{label}", {"PYTHONPATH": str(tree)})
        proc = run([venv_python, "-m", "pytest", target, "-q"], cwd=tree, env=env,
                   timeout=3600, check=False)
        failures = sorted({line.split(" - ")[0].strip()
                           for line in proc.stdout.splitlines() if line.startswith("FAILED")})
        summary = [l for l in proc.stdout.splitlines() if " passed" in l or " failed" in l]
        out[label] = {"failures": failures, "summary": summary[-1:] and summary[-1]}
    # The new file exists only on the composed side; it must pass outright.
    env = disposable_env(work, "pytest_new", {"PYTHONPATH": str(composed)})
    new_file = "tests/tui_gateway/test_gui_surface_toolsets.py"
    proc = run([venv_python, "-m", "pytest", new_file, "-q"], cwd=composed, env=env,
               timeout=1800, check=False)
    out["both_pr_tests"] = {
        "returncode": proc.returncode,
        "summary": ([l for l in proc.stdout.splitlines() if " passed" in l or " failed" in l]
                    or [""])[-1],
    }
    out["pass"] = (out["composed"]["failures"] == out["base"]["failures"]
                   and out["both_pr_tests"]["returncode"] == 0)
    return out


def _wire_surface(work: Path, venv_python: Path) -> dict:
    """Derive the expected wire set from the bridge, rather than assuming 8.

    Current upstream only generates the four MCP resource/prompt utility tools
    when the server advertises those capability families, so whether the
    boundary is 8 names or 4 is a property of this bridge and must be measured.
    """
    from runners.hermes import HERMES_EXPECTED_TOOLS  # local: no Hermes import

    store = work / "synthetic" / "wire_probe.sqlite"
    store.parent.mkdir(parents=True, exist_ok=True)
    _build_scenario_store(store, "A_unanswered_question")
    env = disposable_env(work, "wire_probe", {"SYNTH_DB": str(store)})
    proc = run([venv_python, HERE / "_mcp_capability_probe.py"], env=env, timeout=300,
               check=False)
    if proc.returncode != 0:
        return {"pass": False, "error": proc.stderr[-1500:]}
    observed = json.loads(proc.stdout)
    generated = 4 if (observed["advertises_resources"] and observed["advertises_prompts"]) else 0
    expected = sorted(HERMES_EXPECTED_TOOLS)
    return {
        **observed,
        "generated_utility_tools": generated,
        "derived_wire_count": len(observed["tools"]) + generated,
        "expected_wire_set": expected,
        "pass": len(observed["tools"]) + generated == len(expected) == 8,
    }


def _build_scenario_store(path: Path, key: str) -> Path:
    sys.path.insert(0, str(REPO / ".hermes" / "skills" / "wechat-digest" / "evaluation"))
    from scenarios import SCENARIOS_BY_KEY, build_database
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)      # a rerun must build, not append to, a store
    return build_database(path, SCENARIOS_BY_KEY[key])


def _exact8_probe(work: Path, venv_python: Path, port: int = 8831) -> dict:
    """One Desktop turn against the assertion proxy in probe mode.

    Probe mode never forwards, so this needs no credential and sends nothing
    to a provider: a dummy key is enough to get the runtime as far as building
    the request, which is where the tool surface becomes observable.
    """
    from hermes_online import DEFAULT_DUMMY_KEY, Proxy, desktop_turn_argv, wire_verdict

    db = _build_scenario_store(work / "synthetic" / "exact8_probe.sqlite",
                               "A_unanswered_question")
    env = disposable_env(work, "exact8_probe", {
        "HERMES_DESKTOP": "1",
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_API_KEY": DEFAULT_DUMMY_KEY,
    })
    write_required_config(env, python=venv_python, db=db,
                          base_url=f"http://127.0.0.1:{port}")
    with Proxy(work, "probe", port, mode="probe") as proxy:
        out = work / "exact8_probe_report.json"
        out.unlink(missing_ok=True)
        proc = run(desktop_turn_argv(venv_python, work / "composed",
                                     work / "exact8_probe_response.txt", out),
                   env=env, timeout=1200, check=False)
        wire = proxy.verdict()
    detail = (json.loads(out.read_text(encoding="utf-8")) if out.is_file()
              else {"error": "no report written", "stdout": proc.stdout[-800:]})
    verdict = wire_verdict(wire, "desktop (probe mode, no provider call)", proxy.state)
    verdict["agent_tool_names"] = detail.get("agent_tool_names")
    verdict["session_platform"] = detail.get("session_platform")
    verdict["enabled_toolsets"] = detail.get("enabled_toolsets")
    verdict["pass"] = (verdict["pass"] and detail.get("session_platform") == "desktop"
                       and detail.get("enabled_toolsets") == EXPECTED_DESKTOP_TOOLSETS)
    return verdict


def _repo_tests(work: Path, venv_python: Path) -> dict:
    env = disposable_env(work, "repo_tests")
    proc = run([venv_python, "-m", "pytest", "-q"], cwd=REPO / "shadow", env=env,
               timeout=3600, check=False)
    summary = [l for l in proc.stdout.splitlines() if " passed" in l or " failed" in l]
    return {"returncode": proc.returncode, "summary": (summary or [""])[-1],
            "pass": proc.returncode == 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--phase", choices=("prepare", "offline", "online"), required=True)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--venv-python", type=Path,
                    help="interpreter with pytest and Hermes's dependencies")
    args = ap.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    report_path = args.report or args.work / f"report_{args.phase}.json"
    previous = args.work / "report_prepare.json"
    report: dict = json.loads(previous.read_text()) if previous.is_file() else {}
    report.pop("error", None)         # a prior run's failure is not this run's
    report.pop("traceback", None)
    report["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    try:
        if args.phase == "prepare":
            phase_prepare(args.work, report)
        elif args.phase == "offline":
            if not args.venv_python:
                raise ValidationError("--venv-python is required for the offline phase")
            phase_offline(args.work, report, args.venv_python)
        else:
            from hermes_online import phase_online
            phase_online(args.work, report, args.venv_python)
    except Exception as exc:            # a crash must still leave a report behind
        import traceback
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()[-3000:]
        report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"{args.phase}: FAIL — {exc}", file=sys.stderr)
        return 1
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    verdict = report.get(args.phase, {}).get("pass")
    print(f"{args.phase}: {'PASS' if verdict else 'FAIL' if verdict is False else 'done'}; "
          f"report at {report_path}", file=sys.stderr)
    return 0 if verdict is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
