"""Compile explicit playback/replay/hold decisions to a frame-aligned timeline.

No scene analysis, boundary guessing, audio synthesis, or silent narration shifting.
All seconds use the named source clock; media_origin maps a proxy to that clock.
"""
from copy import deepcopy
import math


def number(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label}: expected finite number >= {minimum}")
    return float(value)


def overlaps(a, b):
    return min(a[1], b[1]) - max(a[0], b[0]) > 1e-6


def merged(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1] + 1e-6:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def compile_plan(plan, audio_durations):
    if plan.get("schema_version") != 1:
        raise ValueError("presentation plan schema_version must be 1")
    fps = number(plan["fps"], "fps", 1)
    sources = deepcopy(plan["sources"])
    for sid, source in sources.items():
        origin = number(source.get("media_origin", 0), f"{sid}.media_origin")
        duration = number(source["media_duration"], f"{sid}.media_duration", 0.001)
        source.update(media_origin=origin, media_duration=duration)
    operations, ids, cursor = [], {}, 0
    for raw in plan["operations"]:
        op = deepcopy(raw)
        oid = op["id"]
        if not isinstance(oid, str) or not oid or oid in ids:
            raise ValueError("operation ids must be unique nonempty strings")
        kind = op["type"]
        if kind not in {"play", "replay", "freeze"}:
            raise ValueError(f"{oid}: unsupported operation {kind}")
        source = sources[op["source_id"]]
        start = number(op["source_start"], f"{oid}.source_start")
        if kind == "freeze":
            end = start
            duration = number(op["duration"], f"{oid}.duration", 1 / fps)
            if op.get("audio") != "mute":
                raise ValueError(f"{oid}: freeze must have explicit muted source audio")
        else:
            end = number(op["source_end"], f"{oid}.source_end")
            duration = end - start
            if duration < 1 / fps:
                raise ValueError(f"{oid}: empty source interval")
        if start < source["media_origin"] or start >= source["media_origin"] + source["media_duration"] or end > source["media_origin"] + source["media_duration"] + 1e-6:
            raise ValueError(f"{oid}: outside source media")
        if op.get("audio") not in {"original", "mute"}:
            raise ValueError(f"{oid}: explicit audio policy required")
        if kind == "replay" and op["audio"] != "mute":
            raise ValueError(f"{oid}: v1 replay is narration-only; repeated dialogue must not leak")
        if not op.get("purpose", "").strip():
            raise ValueError(f"{oid}: missing presentation purpose")
        frames = max(1, round(duration * fps))
        op.update(source_start=start, source_end=end, media_start=start-source["media_origin"],
                  output_start=cursor/fps, output_end=(cursor+frames)/fps, frames=frames, duration=frames/fps)
        ids[oid] = op
        operations.append(op)
        cursor += frames
    if not operations:
        raise ValueError("empty presentation")
    for i, op in enumerate(operations):
        if op["type"] == "replay":
            target = ids.get(op.get("return_to"))
            if target is None or target["type"] != "play" or target["output_start"] <= op["output_start"]:
                raise ValueError(f"{op['id']}: replay needs a later play return_to")
            seen = [(p["source_start"], p["source_end"]) for p in operations[:i] if p["source_id"] == op["source_id"] and p["type"] == "play"]
            if not any(a <= op["source_start"]+1e-6 and b >= op["source_end"]-1e-6 for a,b in merged(seen)):
                raise ValueError(f"{op['id']}: replay must reference already-shown material")

    protected = []
    for window in plan.get("protected_audio", []):
        op = ids[window["operation_id"]]
        start = number(window["source_start"], "protected.source_start")
        end = number(window["source_end"], "protected.source_end")
        if op["type"] != "play" or op["audio"] != "original" or end <= start or start < op["source_start"] or end > op["source_end"]:
            raise ValueError("protected audio must be retained inside an original-audio play")
        protected.append({**window, "output_start":op["output_start"]+start-op["source_start"],
                          "output_end":op["output_start"]+end-op["source_start"]})
    narrations, narr_ids = [], set()
    for item in plan.get("narrations", []):
        nid = item["id"]
        if nid in narr_ids or not item.get("text", "").strip():
            raise ValueError("duplicate or empty narration")
        narr_ids.add(nid)
        duration = number(audio_durations[nid], f"{nid}.audio_duration", .001)
        anchor = ids[item["at_operation"]]
        offset = number(item.get("offset", 0), f"{nid}.offset")
        if offset >= anchor["duration"]:
            raise ValueError(f"{nid}: anchor offset exceeds operation")
        start, end = anchor["output_start"]+offset, anchor["output_start"]+offset+duration
        if end > cursor/fps + 1e-6:
            raise ValueError(f"{nid}: speech exceeds timeline")
        window_ids = item.get("allowed_operations", [item["at_operation"]])
        allowed = merged([(ids[k]["output_start"], ids[k]["output_end"]) for k in window_ids])
        if not any(a <= start+1e-6 and b >= end-1e-6 for a,b in allowed):
            raise ValueError(f"{nid}: speech exceeds authored evidence window; rewrite or replan, never shift automatically")
        if any(overlaps((start,end),(x["start"],x["end"])) for x in narrations):
            raise ValueError(f"{nid}: overlapping narration")
        guard = number(plan.get("handoff_guard", .2), "handoff_guard")
        if any(overlaps((max(0,start-guard),end+guard),(x["output_start"],x["output_end"])) for x in protected):
            raise ValueError(f"{nid}: narration/ducking would overlap protected original audio")
        for event in item.get("after_events", []):
            shown = ids[event["operation_id"]]
            when = number(event["source_time"], "event.source_time")
            if shown["type"] != "play" or not shown["source_start"] <= when <= shown["source_end"]:
                raise ValueError(f"{nid}: invalid event anchor")
            if start < shown["output_start"] + when-shown["source_start"] -1e-6:
                raise ValueError(f"{nid}: narration reveals event before it is shown")
        for cue in item.get("proof_cues", []):
            a = number(cue["audio_start"], "cue.audio_start")
            b = number(cue["audio_end"], "cue.audio_end")
            if not 0 <= a < b <= duration+0.05:
                raise ValueError(f"{nid}: invalid proof cue")
            displays = merged([(ids[k]["output_start"],ids[k]["output_end"]) for k in cue["operation_ids"]])
            if not any(x <= start+a+.05 and y >= start+b-.05 for x,y in displays):
                raise ValueError(f"{nid}: spoken observation has no simultaneous proof")
        narrations.append({**item, "start":start, "end":end, "narration":item["text"],
                           "audio_duration":duration, "pause_after_ms":150, "overlaps_speech":True})
    narrations.sort(key=lambda x:x["start"])
    return {"schema_version":1,"fps":fps,"duration":cursor/fps,"sources":sources,
            "operations":operations,"narrations":narrations,"protected_audio":protected,
            "validation":{"passed":True,"scope":"timing and declared evidence, not artistic quality or factual truth"}}
