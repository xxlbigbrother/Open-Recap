---
name: openrecap
description: 从本地电影制作中文解说成片。统一调度视频理解、背景研究与解说创作、剪辑、配音、字幕混音，支持分 Part 和看片修改。用于用户要求完整制作或继续制作电影解说。
---

# OpenRecap 完整电影解说流程

## 1. 入口与环境

包根目录是本文件上两级。完整保留整个仓库；模型适配和统一入口由包内脚本提供。安装与凭证见 [运行说明](../../怎么运行.md)。

首次运行 `bash setup.sh`，将模型密钥写入私有 `.env`，再运行 `.venv/bin/python run.py doctor`。已有环境变量优先；`RECAP_ENV_FILE` 可指向包外配置。检查只报告是否配置，不显示密钥、不发模型请求。

默认 Gemini3.8 Flash 负责视觉与全局索引，豆包 Seed ASR2.0 负责原声转写，Seed2.1 Pro260628 审稿，MiMo 茉莉原速配音。用户指定的范围、声音和速度优先；其他模型配置见 [模型配置](../../references/model-config.md)。

## 2. 完整技能链

按工作需要读取对应技能，Agent 持续完成创作和执行，不让用户手动串接阶段：

1. [视频理解](../video-understanding/SKILL.md)：场景、抽帧、对白、人物关系与剧情索引。
2. [解说创作](../video-script/SKILL.md)：有来源的电影背景、连续稿、证据与原声任务、统一候选评审。
3. [画面剪辑](../video-cut/SKILL.md)：普通播放、回看、定格与源时间映射。
4. [配音](../video-voiceover/SKILL.md)：按项目音色合成完整旁白，测量实际时长并缓存。
5. [合成](../video-assemble/SKILL.md)：字幕、原声保护、混音、响度和成片检查。

这是一个包含六个 skill 的完整代码库。上述列的是创作分工；执行时先测配音时长，再编译画面、合成。

## 3. 启动与准备创作

核对原片、音轨、真实时长、期望覆盖范围和观众。默认面向没看过电影也能跟上、并得到额外理解的观众。分 Part 按事件自然结束位置选择，时长是可调整目标。

从包根运行，路径有空格时加引号：

```bash
.venv/bin/python run.py run --video "/absolute/path/movie.mp4" \
  --work-dir work/my-film-part1 --title "实际电影名" --start 0 --end 1800
```

`--end` 是希望解说的原片结束秒数，不是成片目标时长；省略即整片。理解分析整个输入视频，第二阶段按所选范围读取。多音轨先确认所需音轨，并生成选定音轨副本，让理解和剪辑使用同一素材。默认使用第一条音轨。

统一入口生成 `project.json`，运行理解、准备证据，并返回 `needs_authoring`。**这是交给 Agent 继续创作的状态，不是向用户交付或请求确认的理由。** 读取返回的 `handoff`、本次 `prepared_dir` 和相关原片画面。

## 4. 研究、写稿与续跑

按解说创作技能完成背景研究与连续稿；遵循 [知识型精讲约定](../../references/commentary-style.md)。影片身份与有助于进入当前故事的背景放在开头，其余知识放在观众需要的画面。

研究脚本可生成线索，但 Agent 必须打开来源核实。将采用的知识写入 `stage2_research.json`，在项目填写 `research_path`；结构见创作技能的研究指南。新增旁注写入 `verified_notes`，不改原始理解。

资料改变后重新准备证据：

```bash
.venv/bin/python scripts/run_skill.py video-script editorial.py \
  --project work/my-film-part1/project.json \
  --work-dir work/my-film-part1/editorial --prepare-only
```

先写连续的 `author_draft.md`，再按当前证据 ID 编排 `editorial/recap_story_plan.candidate.json`。候选结构以创作技能脚本中的 `STORY_SHAPE` 和校验器为准；案例只教方法，不提供新影片事实。

然后重复统一入口：

```bash
.venv/bin/python run.py run --project work/my-film-part1/project.json
```

入口发现候选后，自动审稿、配音、按真实音长编译画面、合成及完整解码。候选未通过返回 `needs_review`，Agent 定位 findings、修稿、再次提交；不要用旧接受稿掩盖失败。配置或素材缺失时只向用户索取确实缺少的信息；同一外部失败重复时保留状态和日志，不无限重试。

旁白超时应修改完整表达或有价值的画面，不截尾或额外提速。知识解读结束接回同一人物、动作或后果。原声问答、喜剧落点和情绪停顿完整保留；回看与定格按观看价值选择，不规定次数。

## 5. 状态与交付

`run.py status --project ...` 查看当前状态。`complete` 表示本次技术检查、输出存在及完整解码通过；输入或产物被改动会标为 `stale`，应重新运行。

交付 `delivery/recap_项目id.mp4`、`editorial/narration_draft.md` 和必要知识来源，标明原片覆盖范围与 Part。抽查中文字幕和接点，并正常速度连续观看、只听声音复核。技术通过不代表已经完成艺术效果验收；准确说明实际检查范围。
