# OpenRecap

给 Agent 使用的完整中文电影解说工具库：**视频理解 → 资料研究与连续稿 → 剪辑规划 → 配音 → 字幕混音与成片**。支持按剧情分 Part、原声交接、回看和定格。

## 安装与启动

需要 Python 3.11+、系统 FFmpeg/ffprobe、中文字体，以及可访问 AIHub 的网络。当前安装入口支持 macOS/Linux。

```bash
git clone https://github.com/xxlbigbrother/Open-Recap.git
cd Open-Recap
bash setup.sh
```

安装会准备本地环境和 `.env` 模板。编辑 `.env`：`AIHUB_API_KEY` 用于 Gemini，`APP_ID` / `APP_KEY` 用于豆包与 MiMo；凭证不随仓库提供。然后检查：

```bash
.venv/bin/python run.py doctor
```

在能读本地文件、执行命令和联网研究的 Agent 中打开仓库，发送一句话：

> 读取 skills/SKILL.md，用「我的原片路径」制作第一部分中文电影解说。先介绍有来源的电影背景，让新观众能跟上剧情，使用茉莉女声。请完成理解、研究、创作、配音、剪辑和成片，输出可播放视频。

Agent 按技能自动接力。脚本统一入口是：

```bash
.venv/bin/python run.py run --video "/absolute/path/movie.mp4" \
  --work-dir work/my-film-part1 --title "实际电影名" --start 0 --end 1800
```

首次运行完成理解并返回 `needs_authoring`，Agent 根据任务文件研究、写候选，再运行同一项目完成后续制作。**一句话完成是 Agent 工作流；单独执行脚本不会代替 Agent 写出已核实的优质稿件。** 细节见 [怎么运行](怎么运行.md)。

## 六个完整 skill

- [openrecap](skills/SKILL.md)：总流程、任务接力和交付。
- [video-understanding](skills/video-understanding/SKILL.md)：画面、对白与剧情理解。
- [video-script](skills/video-script/SKILL.md)：电影背景研究、风格、连续稿与审稿。
- [video-cut](skills/video-cut/SKILL.md)：播放、回放、定格与时间编排。
- [video-voiceover](skills/video-voiceover/SKILL.md)：MiMo / 豆包配音、实际音长与缓存。
- [video-assemble](skills/video-assemble/SKILL.md)：字幕、原声保护、混音和成片。

总入口、五个阶段技能和共享参考库都在同一个 `skills/` 文件夹：

```text
skills/
├── SKILL.md                openrecap 总入口
├── video-understanding/    视频理解
├── video-script/           解说创作
├── video-cut/              画面剪辑
├── video-voiceover/         配音
├── video-assemble/         合成
├── references/             解说风格、方法与参考案例
│   ├── commentary-style.md  讲述约定
│   ├── styles/              可选风格版本
│   └── cases/               学习案例
└── agents/                 总入口的 Agent 展示信息
```

各阶段保留自己的执行代码与专用格式说明。风格统一由 `references/` 提供，通过项目的 `style_path` 选择。公共模型适配和启动辅助在仓库根 `scripts/`，回归测试在 `tests/`。

## 默认配置与验证范围

视觉与全局索引使用 Gemini3.8 Flash，ASR 使用豆包 Seed ASR2.0，审稿使用 Seed2.1 Pro260628，项目默认 MiMo 茉莉原速。详见 [模型配置](skills/references/model-config.md) 和 [讲述风格](skills/references/commentary-style.md)。

已有版本用《功夫》《钢铁侠》进行过真实实验。代码测试包含实际合成素材的剪辑、音频、字幕及时间校验；字幕主要按文字估时，资料与理解仍需核实，技术检查不替代看片。

开发者运行全部测试：

```bash
.venv/bin/python scripts/test.py
```

## 来源

基于 [zenstory-ai/video-recap-skills](https://github.com/zenstory-ai/video-recap-skills) 适配，保留 [上游 MIT 许可](LICENSE.upstream) 与 [来源说明](NOTICE.md)。OpenRecap 代码采用 [MIT](LICENSE)。原片、参考视频、配音和真实配置不包含在仓库中。
