#!/usr/bin/env python3
"""Search source-backed film metadata for commentary planning.

The output is research context, not a finished narration. It deliberately lives
after video understanding so changing web sources never invalidates ASR/VLM
artifacts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


USER_AGENT = "OpenRecap-film-research/1.0"
WIKIPEDIA_FIELDS = "extracts|info|pageprops"
ENTITY_PROPERTIES = {
    "director": "P57",
    "screenwriter": "P58",
    "cast": "P161",
    "composer": "P86",
    "production_company": "P272",
    "country": "P495",
    "genre": "P136",
    "awards": "P166",
}


def request_json(url: str, timeout: float = 20.0) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise RuntimeError("Research response exceeded 8 MiB")
    return json.loads(raw.decode("utf-8"))


def request_text(url: str, timeout: float = 20.0) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 OpenRecap-film-research/1.0", "Accept": "text/html"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise RuntimeError("Research response exceeded 8 MiB")
    return raw.decode("utf-8", "replace")


def strip_html(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def decode_duckduckgo_url(value: str) -> str:
    url = html.unescape(value)
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    return query.get("uddg", [url])[0]


def search_web(title: str, year: int | None, limit: int = 8) -> list[dict]:
    query = f"{title} {year or ''} 电影 导演 编剧 上映 创作背景".strip()
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    body = ""
    # DuckDuckGo's HTML endpoint is more stable with a browser-like POST. The
    # Python urllib GET is kept as a fallback for environments without curl.
    if shutil.which("curl"):
        result = subprocess.run(
            [
                "curl", "-L", "--max-time", "20", "-A",
                "Mozilla/5.0 OpenRecap-film-research/1.0", "-sS",
                "--data-urlencode", f"q={query}",
                "https://html.duckduckgo.com/html/",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            body = result.stdout
    if "result__a" not in body:
        try:
            body = request_text(url)
        except Exception:
            body = ""
    links = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S
    )
    snippets = re.findall(
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', body, re.S
    )
    results = []
    for index, (href, raw_title) in enumerate(links[:limit]):
        results.append({
            "title": strip_html(raw_title),
            "url": decode_duckduckgo_url(href),
            "extract": strip_html(snippets[index]) if index < len(snippets) else "",
            "provider": "duckduckgo-html",
        })
    if not results:
        raise RuntimeError(f"No web search result found for {title!r}")
    return results


def wikipedia_search_url(title: str, year: int | None, language: str, limit: int) -> str:
    query = f'intitle:"{title}" 电影'
    if year:
        query += f" {year}"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 0,
        "gsrlimit": limit,
        "prop": WIKIPEDIA_FIELDS,
        "exintro": 1,
        "explaintext": 1,
        "inprop": "url",
        "format": "json",
        "formatversion": 2,
        "redirects": 1,
        "origin": "*",
    }
    return f"https://{language}.wikipedia.org/w/api.php?{urllib.parse.urlencode(params)}"


def search_wikipedia(title: str, year: int | None, language: str = "zh", limit: int = 5) -> list[dict]:
    payload = request_json(wikipedia_search_url(title, year, language, limit))
    pages = payload.get("query", {}).get("pages", [])
    return [page for page in pages if isinstance(page, dict) and not page.get("missing")]


def choose_wikipedia_page(pages: list[dict], title: str, year: int | None) -> dict:
    if not pages:
        raise RuntimeError(f"No Wikipedia result found for {title!r}")
    wanted = re.sub(r"[\s《》〈〉()（）]", "", title).lower()

    def score(page: dict) -> tuple[float, int]:
        page_title = str(page.get("title") or "")
        extract = str(page.get("extract") or "")
        normalized = re.sub(r"[\s《》〈〉()（）]", "", page_title).lower()
        value = 0.0
        if normalized == wanted:
            value += 8
        elif wanted in normalized:
            value += 5
        if "电影" in extract or "影片" in extract:
            value += 3
        if year and str(year) in extract:
            value += 3
        if "消歧义" in extract or "可指" in extract[:120]:
            value -= 8
        return value, -int(page.get("index", 999))

    return max(pages, key=score)


def wikidata_entity(qid: str) -> dict:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{urllib.parse.quote(qid)}.json"
    payload = request_json(url)
    try:
        return payload["entities"][qid]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"Wikidata entity {qid} is unavailable") from exc


def entity_ids(entity: dict, prop: str, limit: int = 30) -> list[str]:
    values = []
    for claim in entity.get("claims", {}).get(prop, [])[:limit]:
        try:
            value = claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if isinstance(value, dict) and value.get("id"):
            values.append(str(value["id"]))
    return values


def time_values(entity: dict, prop: str) -> list[str]:
    values = []
    for claim in entity.get("claims", {}).get(prop, []):
        try:
            value = str(claim["mainsnak"]["datavalue"]["value"]["time"])
        except (KeyError, TypeError):
            continue
        match = re.match(r"[+-](\d{4})-(\d{2})-(\d{2})", value)
        if match:
            values.append("-".join(match.groups()))
    return list(dict.fromkeys(values))


def quantity_values(entity: dict, prop: str) -> list[dict]:
    values = []
    for claim in entity.get("claims", {}).get(prop, []):
        try:
            value = claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if isinstance(value, dict) and "amount" in value:
            values.append({"amount": str(value["amount"]).lstrip("+"), "unit": value.get("unit")})
    return values


def resolve_labels(ids: list[str], language: str = "zh") -> dict[str, str]:
    unique = list(dict.fromkeys(ids))
    labels: dict[str, str] = {}
    for start in range(0, len(unique), 40):
        batch = unique[start:start + 40]
        params = {
            "action": "wbgetentities",
            "ids": "|".join(batch),
            "props": "labels",
            "languages": f"{language}|zh-hans|zh-hant|en",
            "languagefallback": 1,
            "format": "json",
            "origin": "*",
        }
        url = f"https://www.wikidata.org/w/api.php?{urllib.parse.urlencode(params)}"
        entities = request_json(url).get("entities", {})
        for qid in batch:
            entry = entities.get(qid, {})
            candidates = entry.get("labels", {})
            for key in (language, "zh-hans", "zh", "zh-hant", "en"):
                if candidates.get(key, {}).get("value"):
                    labels[qid] = candidates[key]["value"]
                    break
            labels.setdefault(qid, qid)
    return labels


def localized(entity: dict, field: str, language: str = "zh") -> str:
    values = entity.get(field, {})
    for key in (language, "zh-hans", "zh", "zh-hant", "en"):
        if values.get(key, {}).get("value"):
            return str(values[key]["value"])
    return ""


def first_group(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    if not match:
        return ""
    return next((group.strip() for group in match.groups() if group), "")


def split_names(text: str) -> list[str]:
    return [value.strip(" 《》〈〉") for value in re.split(r"[、，,]|以及|及", text) if value.strip()]


def split_two_names_with_he(text: str) -> list[str]:
    """Split `袁和平和洪金宝` without splitting the 和 inside 袁和平."""
    text = text.strip(" 《》〈〉")
    candidates = []
    for match in re.finditer("和", text):
        left, right = text[:match.start()].strip(), text[match.end():].strip()
        if 2 <= len(left) <= 4 and 2 <= len(right) <= 4:
            candidates.append((abs(len(left) - len(right)), -min(len(left), len(right)), left, right))
    if candidates:
        _, _, left, right = min(candidates)
        return [left, right]
    return split_names(text)


def web_facts(results: list[dict], title: str, year: int | None) -> dict:
    corpus = " ".join(str(item.get("extract") or "") for item in results[:6])
    directors = split_names(first_group(r"(?:由|[，,])\s*([\u3400-\u9fff·]{2,20})\s*(?:担任)?导演|(?:由|[，,])\s*([\u3400-\u9fff·]{2,20})\s*执导", corpus))
    screenwriters = split_names(first_group(r"执导[，,]\s*([^。；]{1,70}?)编剧", corpus))
    if not screenwriters:
        screenwriters = split_names(first_group(r"([\u3400-\u9fff·、，,]{2,80}?)(?:等)?担任编剧", corpus))
    cast = split_names(first_group(r"编剧[，,]\s*([^。；]{1,100}?)等?主演", corpus))
    companies = split_names(first_group(r"《[^》]+》是由([^。；]{1,160}?)出品", corpus))
    action_design = split_two_names_with_he(
        first_group(r"(?:^|[，,。；])\s*([^，,。；]{2,40}?)任动作设计", corpus)
    )
    release = first_group(r"于(20\d{2}年\d{1,2}月\d{1,2}日)" , corpus)
    description = next((str(x.get("extract") or "") for x in results if x.get("extract")), "")
    genre = []
    for candidate in ("动作喜剧", "武侠动作喜剧", "喜剧", "动作片", "武侠片"):
        if candidate in corpus:
            genre.append(candidate)
            break
    return {
        "title": title,
        "description": description,
        "release_dates": [release] if release else ([str(year)] if year else []),
        "runtime": [],
        "director": directors,
        "screenwriter": screenwriters,
        "cast": cast,
        "composer": [],
        "production_company": companies,
        "action_design": action_design,
        "country": ["中国香港"] if "香港" in corpus else [],
        "genre": genre,
        "awards": [],
    }


def web_research(title: str, year: int | None, limit: int) -> dict:
    results = search_web(title, year, limit)
    facts = web_facts(results, title, year)
    directors = facts.get("director", [])
    genres = facts.get("genre", [])
    identity = f"{year}年" if year else ""
    identity += f"电影《{title}》"
    if directors:
        identity += f"，由{'、'.join(directors[:2])}执导"
    if genres:
        identity += f"，是一部{genres[0]}"
    identity += "。"
    sources = [
        {
            "id": f"web-{index + 1}",
            "publisher": urllib.parse.urlparse(item["url"]).netloc,
            "title": item["title"],
            "url": item["url"],
            "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        for index, item in enumerate(results)
    ]
    return {
        "schema_version": 1,
        "query": {"title": title, "year": year, "language": "zh"},
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "researched_with_web_fallback",
        "film": facts,
        "opening_candidates": [
            {
                "kind": "basic_identity",
                "claim": identity,
                "why_useful": "用一句话让第一次接触作品的观众知道片名、年代、类型和主创。",
                "source_ids": [source["id"] for source in sources[:3]],
                "confidence": "medium",
            },
            *([{
                "kind": "creative_team",
                "claim": f"影片的动作设计由{'、'.join(facts['action_design'][:4])}负责。",
                "why_useful": "适合在开场简要建立动作创作背景，或延后到具体打戏比较不同动作风格。",
                "source_ids": [source["id"] for source in sources[:3]],
                "confidence": "medium",
            }] if facts.get("action_design") else []),
            {
                "kind": "background_lead",
                "claim": facts["description"][:1200],
                "why_useful": "作为创作背景候选；使用前应打开来源核对，并只挑与本期角度有关的一条。",
                "source_ids": [sources[0]["id"]] if sources else [],
                "confidence": "low",
            },
        ],
        "search_results": results,
        "sources": sources,
        "usage_policy": {
            "research_is_context_only": True,
            "opening_select_two_to_four_facts": True,
            "external_claims_require_source_ids": True,
            "do_not_narrate_search_snippets_verbatim": True,
            "web_snippets_require_manual_source_check": True,
        },
    }


def research_film(title: str, year: int | None = None, language: str = "zh", limit: int = 5) -> dict:
    try:
        pages = search_wikipedia(title, year, language, limit)
    except Exception:
        return web_research(title, year, limit)
    page = choose_wikipedia_page(pages, title, year)
    qid = str(page.get("pageprops", {}).get("wikibase_item") or "")
    entity = wikidata_entity(qid) if qid else {}
    ids_by_field = {field: entity_ids(entity, prop) for field, prop in ENTITY_PROPERTIES.items()}
    labels = resolve_labels([qid for ids in ids_by_field.values() for qid in ids], language)
    facts = {field: [labels[qid] for qid in ids] for field, ids in ids_by_field.items()}
    release_dates = time_values(entity, "P577")
    runtime = quantity_values(entity, "P2047")
    release_year = year or next((int(value[:4]) for value in release_dates if value[:4].isdigit()), None)
    directors = facts.get("director", [])
    genres = facts.get("genre", [])
    countries = facts.get("country", [])
    identity_bits = []
    if release_year:
        identity_bits.append(f"{release_year}年")
    if countries:
        identity_bits.append(countries[0])
    if genres:
        identity_bits.append(genres[0])
    identity = "".join(identity_bits) + f"电影《{page.get('title') or title}》"
    if directors:
        identity += f"，由{'、'.join(directors[:2])}执导"
    identity += "。"
    source_id = "wikipedia-lead"
    result = {
        "schema_version": 1,
        "query": {"title": title, "year": year, "language": language},
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "researched",
        "film": {
            "title": str(page.get("title") or title),
            "description": localized(entity, "descriptions", language),
            "release_dates": release_dates,
            "runtime": runtime,
            **facts,
        },
        "opening_candidates": [
            {
                "kind": "basic_identity",
                "claim": identity,
                "why_useful": "用一句话让第一次接触作品的观众知道片名、年代、类型和主创。",
                "source_ids": [source_id, "wikidata" if qid else source_id],
                "confidence": "high" if qid and directors else "medium",
            },
            {
                "kind": "background_lead",
                "claim": str(page.get("extract") or "")[:1200],
                "why_useful": "作为创作背景候选，由写稿者筛选一条与本期讲述角度直接相关的信息。",
                "source_ids": [source_id],
                "confidence": "medium",
            },
        ],
        "search_results": [
            {
                "title": str(candidate.get("title") or ""),
                "url": str(candidate.get("fullurl") or ""),
                "extract": str(candidate.get("extract") or "")[:500],
                "wikidata_id": str(candidate.get("pageprops", {}).get("wikibase_item") or ""),
            }
            for candidate in pages
        ],
        "sources": [
            {
                "id": source_id,
                "publisher": f"{language}.wikipedia.org",
                "title": str(page.get("title") or title),
                "url": str(page.get("fullurl") or ""),
                "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
            *([{
                "id": "wikidata",
                "publisher": "Wikidata",
                "title": qid,
                "url": f"https://www.wikidata.org/wiki/{qid}",
                "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }] if qid else []),
        ],
        "usage_policy": {
            "research_is_context_only": True,
            "opening_select_two_to_four_facts": True,
            "external_claims_require_source_ids": True,
            "do_not_narrate_search_snippets_verbatim": True,
        },
    }
    if facts.get("awards"):
        result["opening_candidates"].append({
            "kind": "recognition",
            "claim": f"可核查奖项包括：{'、'.join(facts['awards'][:5])}。",
            "why_useful": "只有当奖项能解释作品影响力且开场确实需要时才选用。",
            "source_ids": ["wikidata"],
            "confidence": "medium",
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Search Wikipedia/Wikidata for source-backed film research")
    parser.add_argument("--title", required=True)
    parser.add_argument("--year", type=int)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--max-results", type=int, default=5)
    args = parser.parse_args()
    if args.max_results < 1 or args.max_results > 10:
        raise ValueError("--max-results must be within 1..10")
    result = research_film(args.title.strip(), args.year, args.language.strip(), args.max_results)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    output = args.work_dir / "film_research.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"[film-research] {result['film']['title']} -> {output}")


if __name__ == "__main__":
    main()
