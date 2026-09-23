---
name: openrecap
description: Use when creating or revising a Chinese film commentary from local video with this OpenRecap bundle, including Gemini understanding, source-backed film background, narrative editing, original-audio handoffs, and MiMo or Doubao narration.
---

# OpenRecap 电影精讲

以本文件所在目录为包根目录。使用包内实际脚本与相对路径；完整安装与命令见[怎么运行](怎么运行.md)。本包可整体移动，不依赖旧实验目录。不要只移动本`SKILL.md`。

默认观众是没看过原片也能跟上、并获得额外理解的人。使用[知识型精讲约定](references/commentary-style.md)，用户指定其他风格或范围时按用户要求处理。

## 模型与环境

- 理解视觉与全局索引：Gemini3.8 Flash，`low`，通过新AIHub标准接口。
- 原片转写：豆包Seed ASR2.0；词时间保留，匿名说话人编号不等于角色身份。
- 第二阶段模型审稿：当前接入的旧AIHub豆包Seed2.1 Pro260628。近期对比中的260915没有被自动替换成生产默认。
- 默认项目配音：MiMo茉莉原速。用户选择音色或速度后，以项目`voice`配置为准。
- API信息见[模型配置](references/model-config.md)。正常运行先检查`smoke_understanding.py --check-config`；它只返回凭证是否存在。

调用阶段统一通过`run_skill.py`，它绑定上述模型。`skill/`包含底层阶段脚本；直接执行它们可能绕过本包模型路由。

`RECAP_ENV_FILE`可指向外部私有配置文件；未指定时读取包根`.env`。已有环境变量优先。配置缺失时只请求缺少的值或位置，不输出Key。源片、配音和真实密钥保持本地，上传范围由用户决定。

## 1. 确定素材与范围

记录源文件、音轨、真实时长、分段范围、观众与声音偏好。分段按事件的自然结束位置决定，时长是可调整目标，不把所有电影均分成固定三段。

已有理解时先核对源身份与时间线，继续消费已有结果。换模型、换源、改变音轨时使用新的工作目录做对照；没有用户要求时不重新分析旧电影。

本包ASR读取输入视频第一条音轨。多音轨电影先明确国语/粤语等实际流，并用媒体副本选择正确音轨，再把同一副本用于理解和项目源。初次运行优先使用单音轨、从0秒开始的完整文件，避免多重偏移。

短测入口生成局部0秒时间线，原片起点在`source-map.json`。做短测解说时将生成的`input.mp4`作为项目源，不能直接把短测的0秒当作原片0秒。

## 2. 获取理解证据

使用`run_skill.py video-understanding understand.py`，其职责和字段见[理解阶段](skill/skills/video-understanding/SKILL.md)。先完成转写、场景/帧事实和全局索引，再进入创作。

读取`vlm_analysis.json`、`asr_result.json`、`understanding_index.json`、`agent_narration_brief.md`和相关抽帧。主线和角色仍需回查原片。把视觉事实、台词、外部知识和解释分清：例如“闭眼”不等于单帧证明停止呼吸，“待在这儿”不证明士兵嫌弃对方。

明确发现的理解错误只写入项目引用的`verified_notes.json`，保留原始理解数据。使用本片实际证据，不把案例里的角色、武功、字幕或知识移植到新片。

## 3. 电影资料与连续稿

按[检索指南](skill/skills/video-script/references/research-guide.md)检索，写`film_research.json`候选资料和`opening_brief.json`。当前精讲开场应交代影片身份，并挑一条能帮助进入故事的背景；适合在具体画面讲的制作知识延后。检索摘要只是线索，采用前打开来源核对。

正式证据构建器读取的是`stage2_research.json`的`facts`格式，**不会自动把`film_research.json`导入**。将核实过的候选转成以下结构，并在项目中设置`research_path`：

```json
{
  "schema_version": 1,
  "source_id": "my-film",
  "facts": [
    {
      "id": "release-context",
      "claim": "实际核实过的背景事实",
      "verified": true,
      "source_url": "实际打开的来源URL",
      "supporting_excerpt": "必要短引文或可核对位置"
    }
  ]
}
```

路径相对项目JSON解析，`source_id`必须与项目源一致。上述是结构说明，示例文字不能当作事实使用。研究改变后重新运行`--prepare-only`，从新的`prepared_dir`读取证据。

按[第二阶段职责](skill/skills/video-script/SKILL.md)和[参考驱动流程](skill/skills/video-script/references/reference-driven-workflow.md)创作：先写可连续口述的`author_draft.md`，同时安排原声的任务、解释发生时机和回到剧情的出口，再编排候选story。

`--prepare-only`只准备资料，不写完成稿。Agent负责选题和写稿；不能通过省略`--candidate`把当前实验性全自动生成当成已经稳定的替代流程。

候选结构以[STORY_SHAPE](skill/skills/video-script/scripts/editorial_prompts.py)和[校验器](skill/skills/video-script/scripts/editorial_contract.py)为准。权威文件为`recap_story_plan.candidate.json`；帧/台词/核实旁注/知识ID只能来自本次`evidence_bundle.json`。研究事实使用`kind: research`，不能写成可见画面观察。

## 4. 评审与配音

调用`run_skill.py video-script editorial.py --candidate ...`。它会做结构检查、一次语义评审，并从一个story投影执行计划。退出码0还需检查状态：`prepared`只准备好，`ready_for_editorial_review`才表示候选获接受；`needs_review`不可直接进入渲染。

正式结果在工作目录根部，历史attempt保留。失败提交会保留上一份接受产物，所以必须核对本次退出状态与候选内容；渲染器只检查稿件与执行计划的指纹，不替你判断是否正在渲染旧版。改正文后重新通过同一入口，不手工分别维护两套正文或更新摘要来掩盖旧稿。

调用`editorial_render.py --audio-only`获得真实音频长度，再用呈现编译检查窗口。超时先调整完整表达或有价值的画面；不要裁语音、无声删字或自动额外加速。新音色更短时，检查是否空等以及对应物件/人物是否及时出现。若改了story，重新评审再渲染。

普通播放、回看、定格支持实际执行；次数和长度由内容决定。保持关键原声问答、停顿、动作与情绪落点完整，避免旁白抢先揭晓。音色控制标签未经实际验证时，不把它写进模型请求后宣称情绪已实现。

## 5. 验证和交付

按[运行说明的检查项](怎么运行.md)查看`editorial_delivery_qc.json`、`assembly_manifest.json`，确认无截断、无额外提速和原声覆盖。抽查中文字幕、画面证据与前后交接，完整解码成片。

交付可播放MP4、带时间的稿件和必要来源说明，标明源范围与Part，不把首章称为全片完成。分别报告机械检查、抽帧、ASR和实际连续听审的范围；艺术效果需要用户看片。修改版本另存，保护用户已认可的局部内容。
