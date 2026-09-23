# OpenRecap

从本地电影或长视频制作中文解说：**Gemini理解 → 资料检索与Agent写稿 → 证据审稿 → MiMo配音 → 剪辑、字幕与混音**。

面向没看过原片也能跟上的观众。开场交代电影背景，正文围绕有依据的观察展开，保留有价值的原声，按需要回看或定格。

- **[怎么运行](怎么运行.md)**：完整安装与执行步骤。
- **[SKILL.md](SKILL.md)**：给Agent读取的总入口，skill名称为`openrecap`。
- **[模型配置](references/model-config.md)**：Key、模型和接口分工。
- **[讲述风格](references/commentary-style.md)**：背景知识、解释、原声和暂停的选择原则。

## 快速开始

推荐Python3.11和macOS/Linux。先安装系统FFmpeg/ffprobe，以及可显示中文的字体。

```bash
git clone https://github.com/xxlbigbrother/Open-Recap.git
cd Open-Recap
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup_tools.py
export PATH="$PWD/tools:$PATH"
cp .env.example .env
chmod 600 .env
```

编辑`.env`：`AIHUB_API_KEY`用于Gemini，`APP_ID`与`APP_KEY`用于豆包ASR、审稿和MiMo语音。也可设置`RECAP_ENV_FILE`指向包外私有文件。需要可访问AIHub的网络，源码不包含模型账号或密钥。

```bash
.venv/bin/python smoke_understanding.py --check-config
.venv/bin/python smoke_understanding.py --video /absolute/path/movie.mp4 --start 0 --seconds 30 --work-dir work/smoke-01
```

在支持本地文件、Python和联网工具的Agent中打开本目录，发送：

> 请读取当前目录SKILL.md，用我提供的原片制作第一部分电影精讲。理解用Gemini，配音用茉莉原速。开场补充有来源的电影背景，让新观众能跟上剧情。核对素材与时间线，研究并写连续稿，再编排原声和镜头，最终输出视频与带时间稿件。

第二部分需要Agent研究、选题和写稿；`--prepare-only`只准备证据，不会自动完成创作。详细命令见[怎么运行](怎么运行.md)。

## 模型与范围

- 视觉理解、全局索引：Gemini3.8 Flash，默认`low`。
- 原片转写：豆包Seed ASR2.0，保留词时间。
- 第二阶段审稿：现有旧AIHub豆包Seed2.1 Pro260628。
- 项目模板配音：MiMo-v2.5-TTS，茉莉原速。

Gemini已完成图片/视频输入、短片理解及第二部分证据交接的真实验证；MiMo茉莉已用于完整首章配音。模型推断仍需回查，字幕当前主要按文字估时，技术检查不替代连续看片。`project.example.json`的示例时长必须换成实际媒体时长。

## 代码结构

```text
SKILL.md                 Agent总入口
run_skill.py             阶段路由与配置
smoke_understanding.py   新目录短片验证
editorial_render.py      配音、时间编译和合成
gemini_adapter.py        Gemini理解接口
aihub_adapter.py         豆包ASR/审稿/语音接口
skill/skills/            六个阶段的脚本与参考
references/              风格和模型配置
tests/                   本地适配测试
skill/tests/             分阶段行为测试
```

底层阶段按独立目录保留，以避免同名模块相互污染。应从根入口运行，直接调用阶段脚本可能绕过本项目模型配置。

## 测试

```bash
PYTHONPATH="$PWD" .venv/bin/python -m pytest test_aihub_adapter.py tests -q
.venv/bin/python skill/scripts/test.py
```

测试不需要真实Key；各阶段在独立进程执行。运行产物保存在忽略的`work/`、`reports/`和`output/`目录。

## 来源与许可

本项目基于MIT许可的[zenstory-ai/video-recap-skills](https://github.com/zenstory-ai/video-recap-skills)适配，保留其许可证及必要来源记录，详见[NOTICE.md](NOTICE.md)。OpenRecap代码采用[MIT](LICENSE)。

仓库不包含原片、参考视频、配音、私人配置、虚拟环境、二进制或缓存。请使用有权处理的素材；示例案例中的事实和反馈属于对应影片，不能直接当作另一部电影的证据。
