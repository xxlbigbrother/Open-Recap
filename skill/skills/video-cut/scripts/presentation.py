"""Render an explicit presentation plan without changing its source/voice anchors."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from presentation_plan import compile_plan


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-2500:])
    return result.stdout


def probe(path):
    return json.loads(run(["ffprobe","-v","error","-show_entries","format=duration:stream=codec_type,width,height,r_frame_rate","-of","json",str(path)]))


def render(compiled, work_dir, width=1280, height=536):
    work = Path(work_dir).resolve()
    parts = work / "presentation_parts"
    parts.mkdir(parents=True,exist_ok=True)
    fps = compiled["fps"]
    paths, manifests = [], []
    for i, op in enumerate(compiled["operations"]):
        source = Path(compiled["sources"][op["source_id"]]["path"]).resolve()
        stat = source.stat()
        source_probe = probe(source)
        observed_duration = float(source_probe["format"]["duration"])
        if op["media_start"] >= observed_duration or (op["type"] != "freeze" and op["source_end"]-compiled["sources"][op["source_id"]]["media_origin"] > observed_duration+.01):
            raise ValueError("source origin/duration differs from media")
        if op["audio"] == "original" and not any(x["codec_type"]=="audio" for x in source_probe["streams"]):
            raise ValueError("original audio requested from silent source")
        payload = {"version":3,"operation":op,"source":str(source),"size":stat.st_size,
                   "mtime_ns":stat.st_mtime_ns,"canvas":[width,height,fps]}
        key = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
        # PCM intermediate audio avoids accumulating one AAC encoder delay per cut.
        part = parts / f"{i:03d}-{key}.mov"
        sidecar = part.with_suffix(".json")
        if not (part.exists() and sidecar.exists()):
            duration = op["duration"]
            cmd=["ffmpeg","-v","error","-y","-ss",str(op["media_start"]),"-i",str(source)]
            if op["audio"] == "mute":
                cmd += ["-f","lavfi","-i","anullsrc=r=48000:cl=stereo"]
            vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
            if op["type"] == "freeze":
                # A one-frame stream can be dropped by fps/tpad at EOF on FFmpeg 7.1.
                # Repeat the captured frame first and assign explicit output timestamps.
                vf = f"trim=end_frame=1,loop=loop=-1:size=1:start=0,setpts=N/({fps}*TB),"+vf
            cmd += ["-map","0:v:0","-map","1:a:0" if op["audio"]=="mute" else "0:a:0","-vf",vf]
            # Only non-contiguous source joins get a small anti-click fade.
            previous = compiled["operations"][i-1] if i else None
            following = compiled["operations"][i+1] if i+1 < len(compiled["operations"]) else None
            def continuous(other, before):
                return other and other["type"]!="freeze" and op["type"]!="freeze" and other["audio"]==op["audio"]=="original" and other["source_id"]==op["source_id"] and abs((other["source_end"]-op["source_start"]) if before else (op["source_end"]-other["source_start"]))<1e-5
            af=["aresample=48000","aformat=channel_layouts=stereo"]
            if not continuous(previous,True): af.append("afade=t=in:d=0.025")
            if not continuous(following,False): af.append(f"afade=t=out:st={max(0,duration-.025)}:d=0.025")
            cmd += ["-af",",".join(af),"-t",str(duration),"-c:v","libx264","-crf","19","-preset","veryfast","-threads","2","-c:a","pcm_s16le","-ar","48000",str(part)]
            print(f"Render {i+1}/{len(compiled['operations'])}: {op['id']}",flush=True)
            run(cmd)
            metadata=probe(part)
            if not any(s['codec_type']=='video' for s in metadata['streams']):
                raise RuntimeError(f"missing rendered video: {op['id']}")
            if abs(float(metadata["format"]["duration"])-duration)>.10:
                raise RuntimeError(f"duration mismatch: {op['id']}")
            sidecar.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
        paths.append(part)
        manifests.append({"operation_id":op["id"],"part":str(part),"fingerprint":key})
    listing = work / "presentation_concat.txt"
    listing.write_text("".join("file '"+str(p).replace("'", "'\\''")+"'\n" for p in paths))
    output = work / "presentation_source.mp4"
    run(["ffmpeg","-v","error","-y","-f","concat","-safe","0","-i",str(listing),"-c:v","copy","-c:a","aac","-b:a","192k","-movflags","+faststart",str(output)])
    actual=probe(output)
    if abs(float(actual["format"]["duration"])-compiled["duration"]) > .12:
        raise RuntimeError("concatenation duration drift")
    manifest={**compiled,"render":{"source":str(output),"media":actual,"parts":manifests}}
    (work/"presentation_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    return output


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--plan",required=True)
    ap.add_argument("--audio-durations",required=True,help="JSON narration_id -> already speed-adjusted duration")
    ap.add_argument("--work-dir",required=True)
    ap.add_argument("--compile-only",action="store_true")
    ap.add_argument("--width",type=int,default=1280)
    ap.add_argument("--height",type=int,default=536)
    args=ap.parse_args()
    plan_path=Path(args.plan).resolve()
    plan=json.loads(plan_path.read_text())
    for source in plan["sources"].values():
        source["path"]=str((plan_path.parent/source["path"]).resolve())
    compiled=compile_plan(plan,json.loads(Path(args.audio_durations).read_text()))
    work=Path(args.work_dir);work.mkdir(parents=True,exist_ok=True)
    (work/"presentation_compiled.json").write_text(json.dumps(compiled,ensure_ascii=False,indent=2)+"\n")
    if args.width<2 or args.height<2 or args.width%2 or args.height%2:raise ValueError('output dimensions must be positive even integers')
    if not args.compile_only:print(render(compiled,work,args.width,args.height),flush=True)


if __name__=="__main__":main()
