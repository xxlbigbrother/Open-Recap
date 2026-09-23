"""ASR time tolerance must agree between consolidation and evidence construction."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _top_level_literal(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path}: missing top-level constant {name}")


def test_asr_span_tol_matches_across_files():
    paths = {
        ROOT / "skills/video-understanding/scripts/consolidate.py",
        ROOT / "skills/video-understanding/scripts/brief_inputs.py",
    }
    values = {
        str(path.relative_to(ROOT)): _top_level_literal(path, "_ASR_SPAN_TOL")
        for path in paths
    }

    assert set(values.values()) == {0.05}, (
        f"_ASR_SPAN_TOL drifted across files: {values}"
    )
