# 显式播放、回放与定格契约 v1

主流程由包内 `scripts/editorial_render.py` 调用 `scripts/presentation.py`，后者通过 `presentation_plan.py` 编译计划。本入口执行作者决定，不自动选片、不修改视频理解、不自动吸附原声边界。作者必须确认真实动作/对白边界，尤其不能在一句电影台词中切走。

## 1. 输入

`--plan` 为 `editorial_plan.json`，`--audio-durations` 为 `{narration_id: 秒数}`，时间必须是已经采用目标语速后的实测配音长度，不能以字数估算。主流程由 `editorial_render.py` 准备音频并写出 `audio_durations.json`。视频源路径相对计划文件解析。

```json
{
  "schema_version": 1,
  "fps": 24,
  "handoff_guard": 0.2,
  "sources": {"movie": {"path": "source.mp4", "media_origin": 45, "media_duration": 100}},
  "operations": [
    {"id":"event","type":"play","source_id":"movie","source_start":50,"source_end":60,"audio":"original","purpose":"先看事件"},
    {"id":"again","type":"replay","source_id":"movie","source_start":52,"source_end":54,"audio":"mute","purpose":"回看刚才证据","return_to":"resume"},
    {"id":"hold","type":"freeze","source_id":"movie","source_start":53.96,"duration":3,"audio":"mute","purpose":"看清姿态"},
    {"id":"resume","type":"play","source_id":"movie","source_start":60,"source_end":70,"audio":"original","purpose":"回到后果"}
  ],
  "narrations": [{
    "id":"n1","text":"回看刚才的证据。","at_operation":"again","offset":0.2,
    "allowed_operations":["again","hold"],
    "after_events":[{"operation_id":"event","source_time":59}],
    "proof_cues":[{"audio_start":0,"audio_end":3,"operation_ids":["again","hold"]}]
  }],
  "protected_audio":[{"operation_id":"resume","source_start":60,"source_end":63,"purpose":"完整台词和反应"}]
}
```

上例对应的 `audio_durations.json` 可为 `{"n1": 4.0}`，表示实际配音为 4 秒。

## 2. 操作与时钟

`media_origin=45` 表示素材文件0秒对应电影45秒，不是全片输出偏移。`media_duration` 是该素材文件长度；素材寻址为 `source_start - media_origin`。源区间必须落在素材覆盖范围内。`freeze` 使用一帧并增加输出时间；它不隐式替换或消费后续源动作。

操作需要唯一非空 `id`、有效 `source_id` 和非空 `purpose`。`play` 正速播放，显式选择 `original` 或 `mute`；`replay` 要求同源片段之前已经由 `play` 展示，并以 `return_to` 明确一个后续 `play` 返回点。v1 回放/定格均静音原声，由下游配音接管。源对白不得在定格中反复播放。

## 3. 旁白与原声保护

`at_operation`＋`offset` 定位旁白开始，`offset` 默认为 0 且必须位于该操作内部。`allowed_operations` 默认为锚定操作，限制整个配音所在的连续有效证据窗口。旁白之间不能重叠；超出证据窗口应重写旁白或重做计划。

`after_events.source_time` 使用源时钟，防止解释提前剧透未发生的动作；`proof_cues.audio_start/end` 使用该句配音内的相对秒数，检查具体语音子句是否同时有画面证据。`protected_audio` 使用指定操作的源时钟，必须完整包含在保留原声的 `play` 中。原声保护检查包括旁白前后各 `handoff_guard` 秒的淡入淡出余量，默认 0.2 秒。

## 4. 执行与输出

```bash
python3 scripts/presentation.py --plan /path/editorial_plan.json \
  --audio-durations /path/audio_durations.json --work-dir /path/work
```

`--compile-only` 只验证并生成 `presentation_compiled.json`。完整渲染另输出 `presentation_source.mp4` 和 `presentation_manifest.json`，中间片段保存在 `presentation_parts/`。每个操作包含源时间、素材时间和输出时间；最终时长按帧累积，旁白永不自动移位、截尾或加速。`--width` 和 `--height` 默认分别为 1280 和 536，必须是正偶数。

渲染中间片段使用PCM音频，避免逐片AAC编码延迟累积；最终合并时统一编码AAC。非连续声音边界只有短防爆音淡入淡出；同源连续运动不额外加音量凹口。

下游以 `presentation_source.mp4` 作为实际视频，用编译后的输出时间写旁白、字幕；需额外生成原声时间映射时只映射保留原声的 `play`，不能给静音回放复制对白。

## 5. 限制

v1 不包含慢放、缩放、叠化、外部资料搜索、配音合成或成片包装。时间检查仅验证作者声明的证据关系，无法独立证明人物判断或镜头解释正确。对中间素材的重复压缩、末端接点与视觉趣味仍需看片检查。
