# 从参考案例到新影片的创作执行

用于用户已经选择一种参考风格、希望生成连续解说与剪辑计划的任务。输入仍是已完成的第一阶段理解，不运行影片理解或改其结果。

## 推荐执行：Agent先写，再验证投影

1. 准备项目JSON，运行 `editorial.py --prepare-only`。阅读输出的 `evidence_bundle.json` 与 `style_snapshot.json`，核对源身份和时间线。参考案例只提供讲述方法，目标影片事实只能来自本项目证据。
2. 先在 `author_draft.md` 规划整个 Part 的大场景路线：普通推进怎样压缩、观察在哪里展开、原声保留哪组完整表演、大场景切换如何交代关系与定位；再写连续口述和原声任务。知识或暂停没有增量时，比较压缩该过程与保留有效表演，不直接退回全程原片播放。不能因模型有暂停能力就每隔一段暂停。
3. 再将稿件与画面共同编排为 `recap_story_plan.candidate.json`。形态遵循 `scripts/editorial_prompts.py` 的 `STORY_SHAPE`；完整字段定义和校验见 `scripts/editorial_contract.py`。段落可以是纯原声，`narration: null`；一段旁白不能同时盖住自己声明保护的原声。
4. 离线验证必须用 `load_project` → `build_evidence` → `load_style` 重建输入，再运行 `validate_story(story, project, evidence, style)`。不要另写一套加载器或只往临时evidence_bundle里加入截图ID；新增目视观察先存入project引用的verified_notes，再用正式入口复核。解决具体问题。把每个主张的证据、问答回收、旁白有效窗口写实；不要删约束来过关。
5. 用 `editorial.py --candidate` 验证并做一次模型语义评审。此模式不自动改Agent稿。通过后统一投影为视听板与执行计划，记录 `authoring_origin=agent_candidate`。
6. 采用实际配音长度调用呈现编译；装不下时改完整口述或有价值的呈现，不额外提速、截尾或移到无关画面。最后检查真实画面、原声交接和字幕，分别报告技术通过与看片范围。

这是当前包的标准创作路径；讲述详略和风格由项目与用户约定决定。`editorial.py` 不代替Agent的观看与判断。

## 项目输入

最小项目字段：`schema_version:1`、`id`、`source{id,path,media_origin,media_duration,range}`、`understanding_dir`、`style_path`。文件路径相对项目JSON解析。

`source.range`与理解证据使用 `movie_source` 时间。已有ASR/VLM元数据须与源身份、artifact指纹相符。若使用剪过的代理媒体，提供 `source_binding{proxy_meta,render_manifest}`，其中代理文件指纹和单一连续源映射要能核对；不能猜一个时间偏移。

可选：`verified_notes`、已核实 `research_path`、`creative_brief`、`required_audio_ranges`、`voice`、`exposure`、`generation`。

### 可选的观察计划

当选题需要比较多个观察或补事实前提时，可由Agent先整理观察计划，再把文件路径填入`development_path`。这是已核对决定的输入接口，当前未默认启用模型自动选题。完整可执行校验在`scripts/editorial_development.py`。

候选字段为`id, observation, evidence_ids, interpretation, viewer_gain, missing_premises, editing_checks, status, return_to_story, presentation_intent`。`missing_premises`列出缺失事实的`claim/action`；`editing_checks`列出剪辑时待核对的`action`。事实足够时`status=ready`，待研究为`needs_research`，放弃为`drop`。`selected_id`选择一个ready候选；尚无可写角度可为null，只能准备输入，不能直接生成。

用正式`load_project → build_evidence`获得当前证据，调用`bind_development(proposal,project,evidence)`绑定身份和摘要，再运行`validate_development`并保存JSON。绑定是版本记录，不能代替实际核对或把模型自报ready升级为真。目标证据改变后，旧观察会返回`stale_development`，应检查受影响前提，而非直接重算摘要盖过问题。

运行器只把选中角度发送到作者、编排者与审稿人；未选研究想法保存在`editorial_development.json`，便于后续跟进。输入原始字节同时用于解析与缓存，避免读到旧内容却绑定新文件散列。成功退出可选流程时，旧当前产物移到新attempt的`superseded/`；失败与prepare-only保留原接受集合。

这一可选接口已在《功夫》准备流程和《钢铁侠》真实创作试验使用。模型写稿仍可能抢先概述笑点、输出过长文字；具体行为证据与边界见工作区`docs/allhands/editorial-development-experiment.md`。它不提高默认自动成片能力的承诺。

- `verified_notes` 是规划层旁注，需 `source_id`、`clock:movie_source`、`notes[{id,start,end,text,evidence,supersedes}]`。只基于实际回查记录冲突，不用自动推断冒充人工核验。
- `required_audio_ranges[{start,end,purpose}]` 是独立于生成稿的原声约束，覆盖关键台词和表演。片段编排和压音不得侵犯。
- `creative_brief` 写清目标、已有前情、要保留的结果和当前方向，不预填目标文案。
- `exposure` 记录是否已看过本段优秀解说。同片复现/迁移练习不能宣称严格未见测试。
- `generation` 仅支持 `temperature` 和 `max_tokens`，实际生效值写入运行记录；项目音色/速度属于项目约定。

## 命令

下列路径为调用者提供的实际文件，命令从包根运行：

```bash
python3 scripts/run_skill.py video-script editorial.py --project /path/project.json --work-dir /path/work --prepare-only
python3 scripts/run_skill.py video-script editorial.py --project /path/project.json --work-dir /path/work --candidate /path/recap_story_plan.candidate.json
```

通过包根入口使用当前项目的模型路由进行评审。

省略 `--candidate` 可做直接模型生成实验。`authoring_mode:write_then_plan` 会先生成口述草稿，再编排；`authoring_draft_path` 可复用已有草稿。**当前真实实验中，固定次数的自动生成仍会失败，尤其是长稿与事件时序冲突；不能把该模式默认为稳定成片能力。** 失败停在 `needs_review`，保留每轮产物，不进配音，也不无限重试。

## 输出与版本

接受稿从一个story统一产生 `visual_audio_board.json` 与 `editorial_plan.json`，均绑定父稿指纹。`narration_draft.md`便于读稿；原声安排与旁白一起阅读。

`input_manifest.json`、`evidence_bundle.json`、`style_snapshot.json`、模型设置及代码指纹随接受稿绑定。每轮候选和失败记录在 `attempts/<run-id>/`。失败重跑或prepare-only不能替换接受稿的输入快照。修改文字后必须重新投影，不能手动改两份不同正文。

`ready_for_editorial_review`只表示结构与模型评审通过，仍需真实音频校准与看片。不同模型、输入或风格版本的结果分别保存；用户认可的局部片段固定保留，不由批量优化覆盖。

## 第一版学习包

`../../references/styles/guided-discovery-v1.json`引用 `../../references/cases/kungfu-discovery-v1.json`。它包含一个已被用户认可的局部实例、两个参考候选、一个需要修订的反例。案例的公开做法、研究解释、用户反馈范围分别记录。

新增电影时提供自己的理解与源映射，复用方法而不复用《功夫》的剧情。案例中的人物、台词、时间和幕后事实不能转为目标影片证据。适用条件不满足时允许不用任何暂停或回放。

## v2内容实验（显式选择）

`../../references/styles/guided-discovery-v2.json`保留v1四案，补入动作引出下一人物、同场不同反应、本领与代价三个参考候选。每案带文本/既有画面观察范围，不等于该片知识事实，也未获用户认可。旧项目继续固定v1。

新增引导与案例已在《钢铁侠》做五份控制稿、五份组合实验稿。两组均存在依赖影片常识和推断过满的问题，因此未升级默认prompt；单次Agent核对成功不代表自动生成稳定。具体诊断与逐稿阅读见工作区`docs/allhands/commentary-value-probe-results.md`。

使用案例时关注完整解释的作用：本片两项具体证据怎样连接、这层关系让观众多懂什么、说完回到哪个后果。普通串场可简短，原声可独立成段；引用标签或一张定格本身不能替代这个内容判断。

### 明确记录语义评审异议

模型评审可能误读纯原声段的制作说明、宽范围旁注或实际语音时间。先定位原finding和真实文件；采纳成立的问题，修稿后重新检查。对仍可证明为误报的语义意见，Agent可写`review_decision_path`，不以无限请求等待偶然pass。

该路径仅与`--candidate`共同使用。决策须包含`schema_version:1`、`reviewer{kind:agent|human,name}`、`verdict:accept`、当前`story_sha256`和`evidence_sha256`、`source_review{path,sha256}`，以及每项error的`decisions[{finding_index,disposition:dismiss,reason,evidence_ids}]`。引用实测时间等附加文件时写`supporting_artifacts{path:sha256}`，文件变更即失效。摘要分别由`story_fingerprint`和`evidence_digest`计算；它们只绑定决定所依据的版本，不证明理由本身正确。

源评审必须是实际保存的attempt对象，含同一story及原findings；其字节和所有裁定保存在接受产物`editorial_adjudication.json`。正式入口仍执行`validate_story`，后续仍检查真实音频和原声保护。接受状态明确为`agent_adjudicated_user_review_pending`或`human_adjudicated`，原机器失败不会被改写为pass。没有裁定文件的项目保持原模型评审路径。

可裁定记录必须由当前运行器在完成语义评审后写出`record_type:editorial_semantic_review`、`review_stage:semantic`、`review_completed:true`、本片source_id和evidence_sha256，finding须有受支持的语义code与具体path/message。结构错误、模型不可用和缺少阶段标记的历史记录不在此入口接受；不能手改旧记录补标记来冒充新评审。需要时从同一候选取得新的正式语义记录。该契约保障本地来源一致性，不提供防恶意伪造的数字签名认证。
