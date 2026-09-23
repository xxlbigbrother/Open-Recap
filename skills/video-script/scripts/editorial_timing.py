"""Output-clock handoffs derived from explicit presentation, never assumed offsets."""
from copy import deepcopy
import math


def map_source_evidence(compiled,asr,acoustic,silence):
    anchors=[];speech=[];quiet=[]
    for op in compiled['operations']:
        if op['audio']=='mute':
            quiet.append({'start':op['output_start'],'end':op['output_end'],'evidence':'rendered_silence'})
            continue
        a,b=op['source_start'],op['source_end'];offset=op['output_start']-a
        for row in acoustic.get('sentence_anchors',[]):
            if a<=row.get('pause_start',row['time']) and row['time']<=b:
                anchors.append({**row,'time':round(row['time']+offset,6),'pause_start':round(row.get('pause_start',row['time'])+offset,6),'source_time':row['time'],'operation_id':op['id']})
        for row in silence:
            if not row.get('has_speech',False):
                start,end=max(a,row['start']),min(b,row['end'])
                if end>start:quiet.append({'start':start+offset,'end':end+offset,'evidence':'existing_source_silence'})
        for row in asr:
            words=row.get('words') or [row]
            for word in words:
                if a<=word['start']<word['end']<=b:
                    speech.append({'start':word['start']+offset,'end':word['end']+offset,'text':word.get('text','')})
    return {'schema_version':1,'timeline':'presentation_output','sentence_anchors':sorted(anchors,key=lambda x:x['time']),
            'speech_spans':speech,'quiet_windows':quiet,'require_measured':True}


def build_tts_meta(compiled,catalog):
    segments=[];providers=set()
    for index,n in enumerate(compiled['narrations']):
        audio=catalog[n['id']]
        provider=audio.get('provider','aihub-doubao');providers.add(provider)
        if audio['text']!=n['text']:raise ValueError(f'{n["id"]}: stale audio text')
        duration=audio['duration']
        if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0 or abs((n['end']-n['start'])-duration)>.002:
            raise ValueError(f'{n["id"]}: audio duration differs from compiled timeline')
        segments.append({**deepcopy(n),'index':index,'narration':n['text'],'spoken_text':n['text'],
            'tts_provider':provider,'tts_model':audio.get('model'),'tts_speaker':audio.get('speaker'),
            'end':n['end']+.02,'audio_path':audio['path'],'audio_duration':duration,'truncated':False,'truncate_reason':'none',
            'tts_rate_offset':0,'global_narration_speed':1,'segment_tempo_factor':1,'effective_tempo':1,
            'preapplied_tempo':audio['post_tempo'],'segment_audio_schema_version':1})
    engine='mimo-tts' if providers=={'aihub-mimo'} else 'doubao-tts' if providers<={'aihub-doubao'} else 'mixed-tts'
    return {'engine':engine,'segments':segments,'partial':False,'failures':[],
            'timing_policy':'audio already speed-adjusted; assembly speed must remain 1, tighten disabled'}
