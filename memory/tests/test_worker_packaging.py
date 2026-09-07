"""The bundled worker's packaging contract (M2.2d shape, M2.2f release gate).

Cheap, offline assertions about the build's *shape*: the pins that make it
reproducible, the properties that let it survive signing, and the nested
bundle identity that a notarization service is entitled to reject. The built
artifact is checked too when it happens to be present.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "scripts" / "build-memory-worker.sh"
EMBED_SCRIPT = ROOT / "scripts" / "embed-memory-worker.sh"
BUILT = ROOT / ".build/memory-worker/dist/MemoryWorker.app"
NESTED_IDENTIFIER = "com.lianghongjing.WeChatCompanion.MemoryWorker"


def build_script() -> str:
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def code_of(path: Path) -> str:
    """A shell script with its comment lines removed.

    The comments here legitimately name the shapes that were rejected -- a
    onefile build, the library-validation entitlement -- in order to say why
    they are *not* used. A scan that read them would fail on the explanation
    instead of the behaviour.
    """
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                      if not line.lstrip().startswith("#"))


def test_the_build_pins_its_tool_and_interpreter():
    source = build_script()
    assert re.search(r'PYINSTALLER_VERSION="\d+\.\d+\.\d+"', source)
    assert 'REQUIRED_PYTHON_MINOR="12"' in source
    # A different interpreter minor must be refused, not silently used.
    assert "Refusing to build with Python" in source


def test_the_build_produces_a_nested_bundle_not_a_loose_directory():
    """onefile fails hardened runtime; a loose onedir tree fails codesign."""
    source = code_of(BUILD_SCRIPT)
    assert "--onedir" in source and "--onefile" not in source
    assert "--windowed" in source


def test_the_nested_bundle_identifier_is_reverse_dns_and_pinned():
    """PyInstaller defaults to the bare product name, which is not a valid
    unique bundle identifier and is a plausible notarization rejection."""
    source = build_script()
    assert f"--osx-bundle-identifier {NESTED_IDENTIFIER}" in source
    # And the build verifies it rather than trusting the flag.
    assert 'Print :CFBundleIdentifier' in source
    assert "expected the reverse-DNS one" in source


def test_the_build_proves_the_worker_is_self_contained():
    assert "env -i" in build_script()


def test_the_embed_step_fails_release_without_a_worker():
    source = code_of(EMBED_SCRIPT)
    assert 'if [[ "${CONFIGURATION:-Debug}" == "Debug" ]]' in source
    assert "error: ${CONFIGURATION} expects a bundled memory worker" in source
    # Signed with the app's identity, not ad-hoc, so Team IDs match.
    assert "--options runtime" in source
    assert "EXPANDED_CODE_SIGN_IDENTITY" in source


def test_no_entitlement_weakens_library_validation():
    """The escape hatch that would have made onefile work is not taken."""
    for path in (BUILD_SCRIPT, EMBED_SCRIPT,
                 ROOT / "apps/WeChatCompanion/WeChatCompanion.xcodeproj/project.pbxproj"):
        source = code_of(path)
        assert "disable-library-validation" not in source
        assert "CODE_SIGN_ENTITLEMENTS" not in source
    assert not list((ROOT / "apps/WeChatCompanion").rglob("*.entitlements"))


@pytest.mark.skipif(not BUILT.exists(), reason="worker not built; run scripts/build-memory-worker.sh")
def test_the_built_bundle_carries_the_pinned_identity():
    contents = plistlib.loads((BUILT / "Contents" / "Info.plist").read_bytes())
    assert contents["CFBundleIdentifier"] == NESTED_IDENTIFIER
    assert contents["CFBundleExecutable"] == "MemoryWorker"
    assert contents["LSBackgroundOnly"] is True
    assert (BUILT / "Contents/MacOS/MemoryWorker").exists()
    # PyInstaller's macOS layout, which is what signs cleanly.
    assert (BUILT / "Contents/Frameworks").is_dir()
