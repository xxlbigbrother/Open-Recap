import json
import sys
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts"))

import research_film
from brief_context import _format_film_research, _format_opening_brief


def test_research_film_builds_source_backed_opening_candidates():
    pages = [{
        "title": "功夫",
        "extract": "《功夫》是2004年上映的香港动作喜剧电影，由周星驰执导。",
        "fullurl": "https://zh.wikipedia.org/wiki/功夫_(电影)",
        "pageprops": {"wikibase_item": "Q123"},
        "index": 1,
    }]
    entity = {
        "descriptions": {"zh": {"value": "2004年香港动作喜剧电影"}},
        "claims": {
            "P57": [{"mainsnak": {"datavalue": {"value": {"id": "Q1"}}}}],
            "P136": [{"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}],
            "P495": [{"mainsnak": {"datavalue": {"value": {"id": "Q3"}}}}],
            "P577": [{"mainsnak": {"datavalue": {"value": {"time": "+2004-12-23T00:00:00Z"}}}}],
        },
    }
    with patch.object(research_film, "search_wikipedia", return_value=pages), patch.object(
        research_film, "wikidata_entity", return_value=entity
    ), patch.object(
        research_film, "resolve_labels", return_value={"Q1": "周星驰", "Q2": "动作喜剧", "Q3": "香港"}
    ):
        result = research_film.research_film("功夫", 2004)
    assert result["film"]["director"] == ["周星驰"]
    assert result["sources"][0]["url"].startswith("https://zh.wikipedia.org/")
    assert "2004年香港动作喜剧电影《功夫》，由周星驰执导" in result["opening_candidates"][0]["claim"]
    assert result["usage_policy"]["do_not_narrate_search_snippets_verbatim"] is True


def test_choose_page_prefers_exact_film_and_year():
    pages = [
        {"title": "功夫", "extract": "武术的别称", "index": 1},
        {"title": "功夫 (电影)", "extract": "2004年上映的电影", "index": 2},
    ]
    assert research_film.choose_wikipedia_page(pages, "功夫", 2004)["title"] == "功夫 (电影)"


def test_web_search_parser_decodes_redirects_and_snippets():
    body = '''
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fkungfu">功夫（电影）</a>
    <a class="result__snippet" href="#">《功夫》是由周星驰执导的&lt;b&gt;动作喜剧&lt;/b&gt;电影。</a>
    '''
    with patch.object(research_film, "request_text", return_value=body):
        rows = research_film.search_web("功夫", 2004)
    assert rows == [{
        "title": "功夫（电影）",
        "url": "https://example.com/kungfu",
        "extract": "《功夫》是由周星驰执导的动作喜剧电影。",
        "provider": "duckduckgo-html",
    }]


def test_action_design_split_preserves_name_containing_he():
    assert research_film.split_two_names_with_he("袁和平和洪金宝") == ["袁和平", "洪金宝"]


def test_research_falls_back_to_web_results():
    results = [{
        "title": "功夫（2004年周星驰电影）",
        "url": "https://example.com/kungfu",
        "extract": "《功夫》是由周星驰执导，曾谨昌、霍昕担任编剧，周星驰、元秋等主演的动作喜剧电影，袁和平和洪金宝任动作设计，于2004年12月23日上映。",
        "provider": "duckduckgo-html",
    }]
    with patch.object(research_film, "search_wikipedia", side_effect=RuntimeError("rate limited")), patch.object(
        research_film, "search_web", return_value=results
    ):
        result = research_film.research_film("功夫", 2004)
    assert result["status"] == "researched_with_web_fallback"
    assert result["film"]["director"] == ["周星驰"]
    assert result["film"]["action_design"] == ["袁和平", "洪金宝"]
    assert result["sources"][0]["url"] == "https://example.com/kungfu"
    assert result["usage_policy"]["web_snippets_require_manual_source_check"] is True


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
