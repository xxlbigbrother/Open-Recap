"""Configuration belongs to the retained stage that actually consumes it."""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIBS = {
    "assemble": ROOT / "skills/video-assemble/scripts/lib.py",
    "script": ROOT / "skills/video-script/scripts/lib.py",
    "understanding": ROOT / "skills/video-understanding/scripts/lib.py",
}

def _load_lib(name, path):
    spec = importlib.util.spec_from_file_location(f"audio_policy_{name}_lib", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def libs():
    return {name: _load_lib(name, path) for name, path in LIBS.items()}


def _readable_source(path, *, include_lib=True):
    """Everything in a skill that could read CONFIG, excluding the CONFIG literal itself."""
    scripts = path.parent
    parts = [
        p.read_text(encoding="utf-8")
        for p in sorted(scripts.glob("*.py"))
        if p.name != "lib.py"
    ]
    if include_lib:
        lib_source = path.read_text(encoding="utf-8")
        config_span = set()
        for node in ast.walk(ast.parse(lib_source)):
            if (
                isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "CONFIG" for t in node.targets)
                and isinstance(node.value, ast.Dict)
            ):
                config_span = set(range(node.lineno, node.end_lineno + 1))
        parts.append(
            "\n".join(
                line
                for number, line in enumerate(lib_source.splitlines(), start=1)
                if number not in config_span
            )
        )
    return "\n".join(parts)


def test_visual_qc_delivery_boundary_fields_are_not_audio_policy_keys(libs):
    """Delivery transparency fields are rollup/QC facts, not shared audio policy knobs;
    this guards against leaking visual-delivery contract fields into CONFIG parity."""
    delivery_fact_keys = {
        "video_encode_passes",
        "reencode_reason",
        "audio_sample_rate",
        "final_compat_notes",
        "double_encode",
    }
    for name, lib in libs.items():
        assert not (delivery_fact_keys & set(lib.CONFIG)), name


def test_no_skill_declares_config_it_never_reads(libs):
    """The invariant that replaces blanket parity.

    A key declared where nothing reads it is dead weight at best and a lie at worst:
    CLIP_PADDING was declared in five CONFIGs, reported as active through
    clip_padding_source, and read by none of them — including the one skill that
    implements padding.
    """
    allowed_unread = {
        # Derived report of a knob this skill implements: FOREIGN_SOURCE_AUDIO selects the
        # ducking volumes below it, and this exposes which policy ended up in effect.
        "assemble": {"foreign_source_audio"},
    }
    offenders = {}
    for name, path in LIBS.items():
        readable = _readable_source(path)
        unread = {
            key
            for key in libs[name].CONFIG
            if f'"{key}"' not in readable and f"'{key}'" not in readable
        }
        unread -= allowed_unread.get(name, set())
        if unread:
            offenders[name] = sorted(unread)
    assert not offenders, (
        f"CONFIG keys declared but read by nothing in their own skill: {offenders}. "
        "Declare a knob in the skill that implements it, not in every copy of lib.py."
    )
