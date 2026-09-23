---
name: video-script
description: >
 消费已有 Gemini 视频理解与核实资料，由 Agent 写连续解说稿和候选 story，
 经 editorial.py 结构校验、语义评审后统一投影视听板与呈现计划。
 适用于中文电影精讲、故事方向、知识解释、原声交接和看片修改。
---

# 第二阶段：Agent 创作与候选评审

本阶段承接 Gemini 理解，交付供 `editorial_render.py` 使用的接受稿与执行计划。
不运行 ASR / VLM，不合成配音或渲染。正常入口由包根 `scripts/run_skill.py video-script editorial.py` 调用，以使用包内模型路由。

## 1. 核对输入与创作范围

项目 JSON 指定源身份、`movie_source` 时间范围、`understanding_dir` 和 `style_path`；路径相对项目文件解析。
读取已有 `vlm_analysis.json`、`asr_result.json`、`understanding_index.json`、`agent_narration_brief.md` 和相关抽帧。
已有理解错误只记入项目引用的 `verified_notes`，保留原理解数据；不得猜测代理媒体的时间偏移。

- **CREATE**：比较可行的故事角度，选择观众承诺、POV、主线和情绪变化。
- **DIRECTED**：落实用户指定的结构、镜头、台词和表达。
- **REVISION**：先记录修改项与冻结项，保留已认可内容，只修改受反馈影响的候选段落。

阅读 [创作剪辑工作法](../references/creative-editing-playbook.md)。重看、回放或知识解释同时参考 [重看式讲述](../references/guided-rewatch.md)。风格和案例统一保存在同级 `../references/`；项目 `style_path` 选择其中 `styles/` 的版本。它们提供讲述方法，不能提供目标影片事实。

长篇精讲、旁白与原声比例失衡或换场生硬时，先读 [节奏与大场景衔接](../references/rhythm-and-scene-handoffs.md)。以完整 Part 规划，不把独立小段按原片顺序拼接当作整体结构。

## 2. 核实电影资料

按 [研究指南](references/research-guide.md) 生成或读取 `film_research.json`，筛选 `opening_brief.json`。
开场交代影片身份，选择能帮助进入故事的背景；制作知识放在有对应证据的时刻。
采用前打开来源核对，区分画面、对白、外部知识与解释。

`film_research.json` 不会自动进入正式证据。将核实事实写成 `stage2_research.json` 的 `facts` 格式，在项目设置 `research_path`；具体字段见研究指南。
研究或旁注变化后重新准备输入，不能把新 ID 直接补进临时证据快照。

## 3. 准备证据、场景路线与连续稿

从包根运行：

```bash
python3 scripts/run_skill.py video-script editorial.py --project /path/project.json --work-dir /path/work --prepare-only
```

阅读本次返回的 `prepared_dir` 中的 `evidence_bundle.json`、`style_snapshot.json` 和输入记录。
`prepared` 仅表示输入准备完成。详细项目契约与版本规则见 [参考驱动流程](references/reference-driven-workflow.md)。

Agent 先在 `author_draft.md` 写大场景路线：本场改变什么，哪些普通推进压缩、哪个观察展开、哪组原声完整体验，以及为什么进入下一场。沿用 `chapters` 表达这些作者选择的叙事单元，不直接采用场景检测的每个镜头作为章节。

再写连续口述、原声任务、信息揭示时机和知识解释后返回剧情的出口。每个大场景边界检查“上一结果 → 换场关系 → 下场人物/地点/目标的必要定位”，把关系落实到可听旁白或可见/可听的原声桥接，不能只写在制作备注。
旁白必须增加上下文、因果、预期或证据支持的解释；画面、原声或沉默足够时让出声音。
句子服从口述与呼吸，不按字幕换行切碎，不追求固定覆盖率、知识点数量或暂停次数。

旁白变短后同时复核选片。保护完整笑点或选择过程，不默认保护整场所有对白；连续多个原声段应合并检查观看任务。删低贡献过程与补有价值解释都可改变比例，禁止用重复概括或机械裁片凑数字。

再编排 `recap_story_plan.candidate.json`，字段以 `scripts/editorial_prompts.py` 的 `STORY_SHAPE` 和 `scripts/editorial_contract.py` 为准。
所有证据 ID 来自本次正式证据包；研究主张使用 `kind: research`。纯原声段允许 `narration: null`。
在同一个 story 中记录呈现操作、原声保护、旁白窗口、事件先后和证据关系，不单独维护另一套执行稿。

自审完整一段：删去无贡献的 beat；检查开头承诺和后文回收；保护完整问答、动作、反应与停顿。
指向当前画面的句子必须在说话时展示具体证据；结果解释不能抢在事件前。回放和定格须有观看价值及明确返回点。

## 4. 提交候选与统一投影

```bash
python3 scripts/run_skill.py video-script editorial.py --project /path/project.json --work-dir /path/work --candidate /path/recap_story_plan.candidate.json
```

入口执行结构检查和一次语义评审，不自动改写 Agent 候选。检查本次状态与内容：

- `ready_for_editorial_review`：候选获接受，仍需真实配音校准与看片。
- `needs_review`：检查本次 attempt 的问题，修订候选后重新提交，不直接渲染。
- `prepared`：只有准备输入，没有接受新稿。

接受稿统一写出 `recap_story_plan.json`、`visual_audio_board.json`、`editorial_plan.json` 和 `narration_draft.md`，执行计划绑定父稿指纹。
失败提交与 prepare-only 保留上一份接受产物，因此根目录存在成品文件不能证明本次候选通过。
不要手改投影文件或摘要来掩盖不同版本。确有证据的语义评审异议按参考驱动流程的 `review_decision_path` 契约处理。

省略 `--candidate` 的模型生成仍是显式实验路径，不能替代默认 Agent 创作流程。

语义评审收到按候选数组顺序计算的 `rhythm_overview`：配音前的估算占比、最长无旁白区间、章节分布与前后交接。它是定位工具，不是硬门槛；缺失场景前提、重复无增量等问题必须指出具体段落。编号跳号和单纯比例偏好不算结构错误。

## 5. 交给渲染与看片

由包根 `scripts/editorial_render.py --audio-only` 获取真实配音长度，再检查呈现窗口与证据同步。
超时先修改完整表达或有价值的画面，不截语音、不无声删字、不额外提速；改稿后重新提交同一候选入口。
检查实际画面、字幕、原声交接和完整声音主线，分别报告机械检查与真实观看、听审范围。

配音后渲染桥会保存 `editorial_rhythm.json`；检查最长连续无旁白区间与所有大场景边界。分章制作时，还要对合并后的整 Part 连续听读和看片，单章评审不能替代跨章检查。 单章提交应在项目 `creative_brief.context_before_range` 简述此前已展示的信息，避免审稿把上下文未提供误判成影片未交代。已有成片计划可独立诊断：

```bash
python3 scripts/run_skill.py video-script editorial_rhythm.py \
  --compiled /path/work/presentation_compiled.json --output /path/work/editorial_rhythm.json
```
