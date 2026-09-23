# 写稿前电影检索与资料筛选

`background_research.json` 用于视频理解前的人名、关系和世界观消歧。`film_research.json` 则在视频理解完成后生成，服务于影片介绍、创作背景、制作知识和解说角度。两者分开，新增或更新电影资料不会自动重跑或改写已有 VLM / ASR 产物。两类资料都属于 `context-only`，不能冒充当前画面证据。

片名明确时，先运行内置搜索：

```bash
python3 scripts/research_film.py --title "功夫" --year 2004 --work-dir <work_dir>
```

脚本搜索 Wikipedia 条目并用 Wikidata 补充导演、编剧、演员、作曲、类型、国家、上映日期、制作公司和奖项，保留页面 URL 与实体 ID。输出 `film_research.json`。它提供可核实的研究起点，不自动生成最终开场文案。

## 工具选择

使用当前环境里可用的任意检索方式即可，例如：

- Codex / Claude 自带的联网搜索或网页浏览工具
- 用户已经配置好的浏览器自动化或页面读取工具
- 普通浏览器手动查找后把结果整理进 JSON
- 已知资料、用户给的剧情背景或项目内已有文档

## 通用流程

1. 从 `--context`、文件名或用户描述里提取可搜索关键词。
2. 运行 `research_film.py` 获取基本身份和候选来源。
3. 对真正影响讲述角度的 1–3 个问题补充可靠来源，例如导演访谈、制作特辑、官方奖项数据库或可信出版物。
4. Agent 写 `opening_brief.json`，只选择 2–4 条开场信息，并说明每条承担什么作用。
5. 把资料与 `vlm_analysis.json` / `asr_result.json` 分开使用：外部资料证明背景，后两者证明当前画面与对白。
6. 没有可靠来源的信息不进入成稿。搜索不可用时，可以使用用户提供的可靠资料，或者明确省略背景信息；不凭模型记忆补齐。

完成研究后，将采用的事实转成 `stage2_research.json`，并在项目设置 `research_path`。`film_research.json` 和 `opening_brief.json` 不会自动进入正式证据包。

```json
{
  "schema_version": 1,
  "source_id": "本项目源ID",
  "facts": [{
    "id": "release-context",
    "claim": "实际核实的背景事实",
    "verified": true,
    "source_url": "实际打开的来源URL",
    "supporting_excerpt": "必要短引文或可核对位置"
  }]
}
```

`source_id` 必须与项目一致，路径相对项目 JSON 解析；示例文字不是事实。研究变化后重新运行 `editorial.py --prepare-only`，阅读本次 `prepared_dir` 的证据。
先写 `author_draft.md`，再编排 `recap_story_plan.candidate.json`；研究主张使用 `kind: research` 和本次正式证据 ID，经 `--candidate` 评审后统一投影视听板与执行计划。

## 按视频类型搜索策略

### 短剧 / 电视剧

| 搜索关键词 | 目标字段 |
|-----------|---------|
| `{作品名} 剧情 介绍 人物` | synopsis, characters |
| `{作品名} 人物 关系` | characters |
| `{作品名} 第{N}集 剧情` | episode_context |

### 电影

| 搜索关键词 | 目标字段 |
|-----------|---------|
| `{电影名} 剧情 简介` | synopsis |
| `{电影名} 影评 解读` | cultural_notes |

### 纪录片 / 科普视频

| 搜索关键词 | 目标字段 |
|-----------|---------|
| `{主题} 背景 知识` | worldbuilding |
| `{核心概念} 解释` | synopsis |
| `{主题} 最新 进展` | cultural_notes |

## `film_research.json`

该文件由搜索脚本生成，主要字段包括 `film`、`opening_candidates`、`search_results` 和 `sources`。每个候选事实包含 `source_ids` 与 `confidence`。

## `opening_brief.json`

由 Agent 筛选后写入：

```json
{
  "one_sentence_intro": "2004年的《功夫》由周星驰执导，是一部把无厘头喜剧与武侠类型结合在一起的电影。",
  "selected_facts": [
    {
      "claim": "开场需要使用的一条创作背景",
      "purpose": "它如何帮助本期讲述，而不是只展示资料量",
      "source_ids": ["wikipedia-lead", "official-interview"]
    }
  ],
  "opening_promise": "观众看完本期会多理解什么",
  "deferred_facts": ["更适合在相关动作或人物出现时再讲的资料"]
}
```

开场先让新观众知道这是什么电影，再尽快进入本期角度。导演、年份、类型通常可以压成一句；奖项、票房、完整演员名单不应为了“资料齐全”全部念出。某条知识如果只能在后面的具体镜头中发挥作用，应放入 `deferred_facts`，到对应场景再讲。

## 旧 `background_research.json` 格式

写入 `work_dir/background_research.json`，只填搜到的字段，未搜到的字段省略，不要写 `null` 或空字符串。

```json
{
  "synopsis": "...",
  "characters": {
    "角色名": "简介或关系"
  },
  "worldbuilding": "...",
  "episode_context": "...",
  "cultural_notes": [
    {"item": "...", "explanation": "..."}
  ]
}
```

## 错误处理

| 场景 | 处理 |
|------|------|
| Wikipedia / Wikidata 不可用 | 保留错误，不写空研究卡；改用浏览器或用户提供的来源 |
| 搜索无结果 | 换一组关键词重试一次，仍无结果则回到当前创作阶段 |
| 结果互相矛盾 | 只使用用户提供的上下文或画面/ASR 可验证的信息 |
| 信息可能剧透后续 | 只用于理解人物关系，不在解说里剧透用户没要求的后续剧情 |

**原则**：背景资料只能补上下文，不能替代 `vlm_analysis.json` / `asr_result.json` 里的画面和对白证据，也不能假装已经被此前的视觉分析消费。搜索提供的是证据候选，创作者仍需判断它是否值得讲、应该在开场还是对应场景出现。
