"""Prepare local FFmpeg/ffprobe launchers without bundling machine-specific binaries."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools-dir',type=Path,default=Path(__file__).resolve().parent/'tools')
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    import imageio_ffmpeg
    ffmpeg=Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    ffprobe=shutil.which('ffprobe')
    if not ffprobe:
        raise SystemExit('Install FFmpeg/ffprobe with your OS package manager, then rerun setup_tools.py')
    filters=subprocess.run([str(ffmpeg),'-hide_banner','-filters'],capture_output=True,text=True,check=True).stdout
    if ' subtitles ' not in filters or ' ass ' not in filters:
        raise SystemExit('Selected FFmpeg lacks subtitles/libass; install a build with those filters')
    if not args.check_only:
        args.tools_dir.mkdir(parents=True,exist_ok=True)
        for name,target in [('ffmpeg',ffmpeg),('ffprobe',Path(ffprobe).resolve())]:
            output=args.tools_dir/name
            if output.exists() or output.is_symlink():
                print(f'{name}: existing local tool preserved')
                continue
            if os.name=='nt':
                raise SystemExit('Windows: add FFmpeg/ffprobe to PATH; this optional symlink helper requires POSIX')
            output.symlink_to(target)
    print('FFmpeg subtitles/libass available; ffprobe found. Install a Chinese-capable font separately.')


if __name__=='__main__':main()
