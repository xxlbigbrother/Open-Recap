"""Research context belongs to the understanding brief that consumes it."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/video-understanding/scripts"))
from lib import CONFIG
from agent_brief import build_agent_brief
from brief_context import (
    assess_understanding_substrate,
    _format_film_research,
    _format_opening_brief,
)


def test_build_agent_brief_research_directive_when_context_without_research(
    monkeypatch, tmp_path
):
    """A title/context with no background_research.json must trigger a loud research-first
    directive (the root of 'cold' narration: no story context -> only pixels to narrate)."""
    monkeypatch.setitem(CONFIG, "edit_mode", "full")
    monkeypatch.setitem(CONFIG, "target_duration", "")
    monkeypatch.setitem(CONFIG, "context_info", "这是《庆余年》第一集")
    scenes = [
        {
            "scene_id": 0,
            "start": 0.0,
            "end": 6.0,
            "description": "范闲登场与人对峙暗藏机锋",
        }
    ]
    asr = [{"start": 1.0, "end": 5.0, "text": "一句对白。"}]
    text = build_agent_brief(scenes, asr, [], 6.0, tmp_path).read_text(encoding="utf-8")
    assert "Research the story FIRST" in text
    assert "庆余年" in text  # the context is echoed into the directive

    (tmp_path / "background_research.json").write_text(
        '{"synopsis": "范闲查案"}', encoding="utf-8"
    )
    text2 = build_agent_brief(scenes, asr, [], 6.0, tmp_path).read_text(
        encoding="utf-8"
    )
    assert (
        "Research the story FIRST" not in text2
    )  # already researched -> directive gone


def test_research_directive_does_not_fire_for_dialogue_rich_titled_run(
    monkeypatch, tmp_path
):
    """Step 3: a dialogue-rich (substrate=rich) titled run with no research file must NOT be
    nagged — the directive fires only for thin/empty substrate, not merely because a title exists."""
    monkeypatch.setitem(CONFIG, "edit_mode", "full")
    monkeypatch.setitem(CONFIG, "target_duration", "")
    monkeypatch.setitem(
        CONFIG, "context_info", "这是《庆余年》第一集"
    )  # a title, but no research file
    scenes = [
        {
            "scene_id": i,
            "start": float(i * 6),
            "end": float(i * 6 + 6),
            "description": "范闲与人对峙",
            "frame_facts": {str(i * 6): ["对峙"]},
        }
        for i in range(4)
    ]
    asr = [{"start": 1.0, "end": 5.0, "text": "对" * 250}]  # rich dialogue spine
    assert assess_understanding_substrate(scenes, asr)["level"] == "rich"
    text = build_agent_brief(scenes, asr, [], 24.0, tmp_path).read_text(
        encoding="utf-8"
    )
    assert "Research the story FIRST" not in text  # rich + titled -> no nag


def test_build_agent_brief_injects_background_research(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "edit_mode", "full")
    monkeypatch.setitem(CONFIG, "target_duration", "")
    monkeypatch.setitem(CONFIG, "context_info", "")
    (tmp_path / "background_research.json").write_text(
        json.dumps(
            {
                "synopsis": "少年范闲深夜查案。",
                "characters": {"范闲": "主角", "五竹": "范闲的护卫"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    brief = build_agent_brief(
        [
            {
                "scene_id": 0,
                "start": 0.0,
                "end": 3.0,
                "description": "夜路",
                "frame_facts": {"1.0": ["走路"]},
            }
        ],
        [{"start": 0.0, "end": 3.0, "text": "你终于来了"}],
        [],
        3.0,
        tmp_path,
    )
    text = brief.read_text(encoding="utf-8")
    assert "Story context" in text
    assert "五竹" in text
    assert "范闲的护卫" in text


def test_build_agent_brief_storyless_rich_video_relaxes_and_prompts_research(
    monkeypatch, tmp_path
):
    """End-to-end for the anime complaint: a frame-fact-rich but storyless video (no
    dialogue, no research) must now be treated as thin so the density relaxes and the
    research directive fires — instead of being graded 'rich' and shipping cold."""
    monkeypatch.setitem(CONFIG, "edit_mode", "full")
    monkeypatch.setitem(CONFIG, "target_duration", "")
    monkeypatch.setitem(CONFIG, "context_info", "")
    scenes = [
        {
            "scene_id": i,
            "start": float(i * 6),
            "end": float(i * 6 + 6),
            "description": "人物在画面里走动" * 3,
            "frame_facts": {str(i * 6): ["走动"]},
        }
        for i in range(6)
    ]
    text = build_agent_brief(scenes, [], [], 36.0, tmp_path).read_text(encoding="utf-8")
    assert "do NOT chase a beat count" in text  # density relaxed (FIX D)
    assert "Research the story FIRST" in text  # research directive (FIX E)
    assert "segments/min (minimum" not in text  # strict quota line suppressed


def test_film_research_is_visible_in_agent_brief_context():
    lines = _format_film_research({
        "film": {"title": "功夫", "director": ["周星驰"], "genre": ["动作喜剧"]},
        "opening_candidates": [{
            "kind": "basic_identity",
            "claim": "2004年的《功夫》由周星驰执导。",
            "why_useful": "快速建立作品身份。",
            "source_ids": ["wikidata"],
        }],
        "sources": [{"id": "wikidata", "title": "Q123", "url": "https://www.wikidata.org/wiki/Q123"}],
    })
    text = "\n".join(lines)
    assert "opening_brief.json" in text
    assert "周星驰" in text
    assert "do not read search extracts verbatim" in text


def test_opening_brief_preserves_selected_facts_and_sources():
    text = "\n".join(_format_opening_brief({
        "one_sentence_intro": "2004年的《功夫》由周星驰执导。",
        "selected_facts": [{
            "claim": "影片融合武侠与喜剧。",
            "purpose": "建立本期观察角度。",
            "source_ids": ["web-1"],
        }],
        "opening_promise": "看懂高手为何藏在普通生活里。",
        "deferred_facts": ["动作指导更替放到具体打戏再讲"],
    }))
    assert "2004年的《功夫》" in text
    assert "web-1" in text
    assert "动作指导更替" in text
