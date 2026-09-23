---
name: video-cut
user-invocable: false
description: >
 用于执行已写好的 presentation plan，处理视频剪辑、正速播放、回放、定格、旁白证据窗口与原声保护。
---

## 1. 定位与入口

本技能执行作者明确选择的呈现操作。主流程由包内 `scripts/editorial_render.py` 调用 `scripts/presentation.py`，后者用 `presentation_plan.py` 编译时间线，再渲染画面与原声。

## 2. 计划与实测配音

输入为 `editorial_plan.json` 和 `audio_durations.json`。计划使用 `schema_version: 1`，包含 `fps`、`sources`、`operations`，以及按需声明的 `narrations`、`protected_audio` 和 `handoff_guard`。完整字段见 [呈现计划契约](references/presentation-plan.md)。

`audio_durations.json` 是 `{旁白ID: 秒数}`，必须来自按目标语速处理后的真实音频。主流程先准备配音，再测量并写入时长；不能用字数估算代替。旁白由 `at_operation` 和 `offset` 定位，整句必须落在 `allowed_operations` 的连续证据窗口内；超长应重写旁白或重做计划，不能自动移位、截尾或额外加速。

## 3. 播放、回放与定格

1. `play`：按 `source_start` 到 `source_end` 正速播放，明确选择 `audio: original` 或 `mute`。
2. `replay`：只回放此前 `play` 已展示的同源片段，必须静音，并以 `return_to` 指向后续的 `play`。
3. `freeze`：在 `source_start` 取一帧，按 `duration` 延长输出，必须静音；不消耗后续源动作。

每个操作都有唯一 `id`、`source_id` 和非空 `purpose`。作者确认动作和对白边界，保持完整台词与自然停顿；脚本不自动选片或吸附句末。

## 4. 源时钟与证据

`source_start/end`、事件锚点和原声保护区间使用对应 `source_id` 的源时钟。`media_origin` 表示素材文件 0 秒对应的源时间，素材寻址为 `source_start - media_origin`；`media_duration` 是真实素材长度。素材路径相对计划文件解析。

输出时间从 0 开始，按操作顺序逐帧累计。`after_events` 要求事件先展示再解说，`proof_cues` 用旁白音频内的相对秒数检查子句与画面是否同步。校验只证明时间和声明的证据关系，人物判断与镜头解释仍需人工核对。

## 5. 原声保护

`protected_audio` 必须位于保留原声的 `play` 内。旁白前后的 `handoff_guard` 余量默认各为 0.2 秒，也不能覆盖保护区间。回放与定格不重复播放对白；下游只为保留原声的 `play` 映射源语音证据。

渲染对非连续声音接点做短防爆音淡入淡出，同源连续原声连接不额外压低音量。中间片段使用 PCM，合并时统一编码 AAC，避免逐片编码延迟累积。

## 6. 执行与输出

在本技能目录下执行：

```bash
python3 scripts/presentation.py --plan /path/work/editorial_plan.json \
  --audio-durations /path/work/audio_durations.json --work-dir /path/work
```

`--compile-only` 只校验并写出 `presentation_compiled.json`。完整渲染还生成 `presentation_source.mp4`、`presentation_manifest.json` 和 `presentation_parts/` 中间片段；`--width`、`--height` 可指定正偶数画布尺寸。

下游使用编译后的输出时钟安排旁白和字幕。本技能不合成配音、不包装字幕，也不包含慢放、缩放或叠化；渲染后仍需看片检查接点和声音交接。
