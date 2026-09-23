"""Describe narration distribution and scene handoffs without an artistic pass/fail."""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import re


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('rhythm times must be finite numbers')
    return float(value)


def _merge(intervals):
    result = []
    for start, end in sorted(intervals):
        start, end = _number(start), _number(end)
        if end < start:
            raise ValueError('reversed rhythm interval')
        if end == start:
            continue
        if result and start <= result[-1][1] + 1e-6:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def _clipped(intervals, left, right):
    return _merge([(max(left, a), min(right, b)) for a, b in intervals if a < right and b > left])


def _seconds(intervals):
    return sum(b-a for a, b in _merge(intervals))


def summarize_compiled(compiled, *, duration_basis='measured_audio'):
    duration = _number(compiled['duration'])
    if duration < 0:
        raise ValueError('negative rhythm duration')
    operations = compiled.get('operations', [])
    narration = _clipped([(_number(n['start']), _number(n['end'])) for n in compiled.get('narrations', [])], 0, duration)
    protected = [(_number(g['output_start']), _number(g['output_end'])) for g in compiled.get('protected_audio', [])]
    source_audio = [(o['output_start'], o['output_end']) for o in operations if o.get('audio') == 'original']
    gaps, cursor = [], 0
    for start, end in narration:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = end
    if cursor < duration:
        gaps.append((cursor, duration))
    runs = []
    for start, end in gaps:
        covered = [o for o in operations if o['output_start'] < end and o['output_end'] > start]
        runs.append({'start': round(start, 6), 'end': round(end, 6), 'duration': round(end-start, 6),
                     'operation_ids': [o['id'] for o in covered],
                     'purposes': list(dict.fromkeys(o.get('purpose', '') for o in covered if o.get('purpose'))),
                     'protected_seconds': round(_seconds(_clipped(protected, start, end)), 6)})
    unique = {}
    for op in operations:
        if op['type'] == 'play':
            unique.setdefault(op['source_id'], []).append((op['source_start'], op['source_end']))
    narration_seconds = _seconds(narration)
    types = {kind: _seconds([(o['output_start'], o['output_end']) for o in operations if o['type'] == kind])
             for kind in ('play', 'replay', 'freeze')}
    return {'schema_version': 1, 'assessment': 'descriptive_only', 'clock': 'output',
            'duration_basis': duration_basis, 'duration_seconds': round(duration, 6),
            'narration_seconds': round(narration_seconds, 6),
            'narration_occupancy': narration_seconds/duration if duration else 0,
            'without_narration_seconds': round(duration-narration_seconds, 6),
            'source_audio_enabled_seconds': round(_seconds(_clipped(source_audio, 0, duration)), 6),
            'source_play_seconds': round(types['play'], 6),
            'source_unique_play_seconds': round(sum(_seconds(x) for x in unique.values()), 6),
            'replay_seconds': round(types['replay'], 6), 'freeze_seconds': round(types['freeze'], 6),
            'longest_without_narration_seconds': round(max((b-a for a, b in gaps), default=0), 6),
            'without_narration_runs': runs,
            'limitations': ['Narration intervals include pauses within each audio file.',
                           'Without narration does not mean silence or movie dialogue only.',
                           'Enabled source audio may be ducked or overlap narration; these are not exclusive shares.',
                           'Long performance runs and narration ratios are review prompts, never automatic failures.']}


def summarize_story(story, project):
    """Estimate before TTS, preserving array order and the author-declared chapter edges."""
    fps = _number(project.get('fps', 24))
    cps = _number(project.get('estimated_chars_per_second', 3.4))
    if fps <= 0 or cps <= 0:
        raise ValueError('rhythm fps and speech estimate must be positive')
    operations, narrations, guards, paragraphs = [], [], [], []
    cursor = 0
    for paragraph in story.get('paragraphs', []):
        start = cursor
        for raw in paragraph.get('presentation', []):
            op = deepcopy(raw)
            length = op.get('duration') if op['type'] == 'freeze' else op['source_end']-op['source_start']
            length = _number(length)
            if length <= 0:
                raise ValueError('rhythm operation duration must be positive')
            length = max(1, round(length*fps))/fps
            op.update(output_start=cursor, output_end=cursor+length)
            cursor += length
            operations.append(op)
        paragraphs.append({'id': paragraph.get('id'), 'chapter_id': paragraph.get('chapter_id'),
                           'start': start, 'end': cursor, 'change': paragraph.get('change'),
                           'incoming_result': paragraph.get('incoming_result'),
                           'handoff': deepcopy(paragraph.get('handoff', {})),
                           'narration_text': (paragraph.get('narration') or {}).get('text')})
    by_id = {o['id']: o for o in operations}
    for paragraph in story.get('paragraphs', []):
        n = paragraph.get('narration')
        if n and n.get('at_operation') in by_id:
            start = by_id[n['at_operation']]['output_start'] + _number(n.get('offset', 0))
            duration = len(re.findall(r'[\u3400-\u9fffA-Za-z0-9]', n['text']))/cps
            narrations.append({'id': n['id'], 'start': start, 'end': start+duration})
        for guard in paragraph.get('protected_audio', []):
            op = by_id[guard['operation_id']]
            guards.append({'output_start': op['output_start']+guard['source_start']-op['source_start'],
                           'output_end': op['output_start']+guard['source_end']-op['source_start']})
    result = summarize_compiled({'duration': cursor, 'operations': operations, 'narrations': narrations,
                                 'protected_audio': guards}, duration_basis='estimated_from_text_before_tts')
    source_range = project.get('source', {}).get('range')
    if source_range and len(source_range) == 2:
        result['source_scope_seconds'] = source_range[1]-source_range[0]
    chapters = []
    definitions = {x['id']: x for x in story.get('chapters', [])}
    for paragraph in paragraphs:
        cid = paragraph['chapter_id']
        if not chapters or chapters[-1]['chapter_id'] != cid:
            chapters.append({'chapter_id': cid, 'title': definitions.get(cid, {}).get('title'),
                             'start': paragraph['start'], 'end': paragraph['end']})
        else:
            chapters[-1]['end'] = paragraph['end']
    for chapter in chapters:
        seconds = _seconds(_clipped([(n['start'], n['end']) for n in narrations], chapter['start'], chapter['end']))
        chapter['narration_seconds'] = round(seconds, 6)
        chapter['narration_occupancy'] = seconds/(chapter['end']-chapter['start']) if chapter['end'] > chapter['start'] else 0
    boundaries = []
    for previous, following in zip(paragraphs, paragraphs[1:]):
        if previous['chapter_id'] != following['chapter_id']:
            boundaries.append({'from_paragraph': previous['id'], 'to_paragraph': following['id'],
                               'from_chapter': previous['chapter_id'], 'to_chapter': following['chapter_id'],
                               'output_time': following['start'], 'outgoing_result': previous['change'],
                               'outgoing_handoff': previous['handoff'], 'previous_narration': previous['narration_text'],
                               'next_incoming_result': following['incoming_result'], 'next_narration': following['narration_text']})
    result.update(chapters=chapters, chapter_handoffs=boundaries)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiled', required=True, help='presentation_compiled.json with actual narration times')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = summarize_compiled(json.loads(Path(args.compiled).read_text()))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'output': str(output), 'narration_occupancy': report['narration_occupancy'],
                      'longest_without_narration_seconds': report['longest_without_narration_seconds']}))


if __name__ == '__main__':
    main()
