"""Exercise real media rendering: freeze holds one frame and adds no repeated audio."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave
import array
import math
import pytest


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='media tools unavailable')
def test_render_has_correct_duration_frozen_picture_and_muted_replay(tmp_path):
 source=tmp_path/'source.mp4'
 subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=96x64:rate=24',
  '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)],check=True)
 plan={'schema_version':1,'fps':24,'sources':{'m':{'path':str(source),'media_origin':45,'media_duration':3}},
 'operations':[
 {'id':'one','type':'play','source_id':'m','source_start':45,'source_end':46,'audio':'original','purpose':'event'},
 {'id':'again','type':'replay','source_id':'m','source_start':45.25,'source_end':45.75,'audio':'mute','purpose':'proof','return_to':'two'},
 {'id':'hold','type':'freeze','source_id':'m','source_start':45.708333,'duration':1.5,'audio':'mute','purpose':'inspect'},
 {'id':'two','type':'play','source_id':'m','source_start':46,'source_end':47,'audio':'original','purpose':'resume'}],
 'narrations':[]}
 (tmp_path/'plan.json').write_text(json.dumps(plan));(tmp_path/'durations.json').write_text('{}')
 entry=Path(__file__).resolve().parents[2]/'skills/video-cut/scripts/presentation.py'
 subprocess.run([sys.executable,str(entry),'--plan',str(tmp_path/'plan.json'),'--audio-durations',str(tmp_path/'durations.json'),'--work-dir',str(tmp_path)],check=True,capture_output=True)
 output=tmp_path/'presentation_source.mp4'
 duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(output)]))
 assert abs(duration-4)<.06
 raw=subprocess.check_output(['ffmpeg','-v','error','-ss','1.75','-i',str(output),'-t','0.9','-vf','scale=96:64,format=gray','-f','rawvideo','-'])
 frame_size=96*64;frames=[raw[i:i+frame_size] for i in range(0,len(raw),frame_size) if len(raw[i:i+frame_size])==frame_size]
 assert len(frames)>=20
 assert max(sum(abs(a-b) for a,b in zip(frames[0],f))/frame_size for f in frames)<.5
 pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-vn','-ac','1','-ar','48000','-f','s16le','-'])
 values=array.array('h',pcm)
 def rms(a,b):
  sample=values[int(a*48000):int(b*48000)];return math.sqrt(sum(v*v for v in sample)/len(sample))
 assert rms(.2,.8)>100
 assert rms(1.15,2.9)<2
 assert rms(3.2,3.8)>100


def test_freeze_from_nonzero_video_timestamp_keeps_video_stream(tmp_path):
    """A single-frame fps filter can discard a frame when EOF is one timestamp later."""
    import importlib.util
    scripts=Path(__file__).resolve().parents[2]/'skills/video-cut/scripts'
    sys.path.insert(0,str(scripts))
    spec=importlib.util.spec_from_file_location('presentation_highfps',scripts/'presentation.py')
    renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
    source=tmp_path/'highfps.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=24:d=3','-f','lavfi','-i','sine=frequency=440:duration=3','-vf','setpts=PTS+1/24/TB','-fps_mode','passthrough','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)],check=True)
    plan={'schema_version':1,'fps':24,'sources':{'m':{'path':str(source),'media_origin':0,'media_duration':3}},'operations':[
        {'id':'before','type':'play','source_id':'m','source_start':0,'source_end':1,'audio':'original','purpose':'event'},
        {'id':'hold','type':'freeze','source_id':'m','source_start':1.2,'duration':.5,'audio':'mute','purpose':'evidence'},
        {'id':'after','type':'play','source_id':'m','source_start':1,'source_end':2,'audio':'original','purpose':'return'}],
        'narrations':[],'protected_audio':[]}
    output=renderer.render(renderer.compile_plan(plan,{}),tmp_path/'work',160,90)
    info=renderer.probe(output)
    assert any(s['codec_type']=='video' for s in info['streams'])
    assert abs(float(info['format']['duration'])-2.5)<.12
    frozen=list((tmp_path/'work/presentation_parts').glob('001-*.mov'))[0]
    frozen_info=renderer.probe(frozen)
    assert any(s['codec_type']=='video' for s in frozen_info['streams'])
