---
name: video-voiceover
user-invocable: false
description: >
 用于已确认旁白的独立配音、音色或语速调整，以及剪辑前取得真实音频时长；支持 AIHub MiMo、豆包与 ElevenLabs 语音。
---

## 1. 定位与输入

本技能把已确认的 `editorial_plan.json` 中 `narrations` 转成真实配音。
每条旁白必须有唯一 `id` 和要朗读的 `text`。只朗读正文，表演说明不写入正文。
`voiceover.py` 不改写稿件、不裁切声音，也不自动决定画面窗口。

`--project` 指向项目 JSON，使用其中的 `voice` 配置；项目内部路径相对该 JSON
所在目录解析。单独配音无需读取源片或理解结果，也无需导入其他技能。
`--plan` 显式选择已确认的计划；三个命令行路径均可使用绝对路径，相对路径从当前目录解析。

## 2. 配音配置

MiMo 配置示例：

```json
{
  "voice": {
    "provider": "aihub-mimo",
    "speaker": "茉莉",
    "tempo": 1
  }
}
```

- `aihub-mimo` 使用 `api_xiaomi_mimo-v2.5-tts`，默认音色 `茉莉`、语速 `1`。
- `aihub-doubao` 使用 `api_doubao_doubao-tts-2.0`，默认音色
  `zh_male_cixingjieshuonan_uranus_bigtts`、语速 `1.1`。省略 provider 时使用豆包。
- `aihub-elevenlabs` 使用 AIHub 原生透传，默认模型 `eleven_v3`、后处理倍率 `1`。
  必须显式提供原生 voice ID 作为 `speaker`，不自动选择或替换音色。
- `tempo` 必须是 `0.5..2` 的有限数字。MiMo/豆包请求保持原生语速 `+0%`；
  ElevenLabs 的模型语速由 `voice_settings.speed` 控制。`tempo` 是额外的本地后处理倍率。

《功夫》Part1 对照采用的 ElevenLabs 配置（Julian 音色，已由主任务完成开场短测）：

```json
{
  "voice": {
    "provider": "aihub-elevenlabs",
    "speaker": "j2FxFb20sd3xlm2pRaM5",
    "model": "eleven_v3",
    "language_code": "zh",
    "voice_settings": {
      "stability": 0.5,
      "similarity_boost": 0.75,
      "style": 0,
      "speed": 1.1
    },
    "tempo": 1
  }
}
```

支持原生模型 ID `eleven_v3`、`eleven_multilingual_v2`、`eleven_flash_v2_5`，
不使用 `api_elevenlabs_...` 标记。可选 `language_code` 使用小写两字母语言码；
v3/Flash 可设 `zh`，省略时由模型判断语言。Multilingual v2 必须省略该字段，显式指定会报错。

`voice_settings` 默认 `stability=0.5`、`similarity_boost=0.75`、`style=0`、`speed=1`。
前三项必须是 `0..1` 的有限数字，且 v3 的 `stability` 只允许 `0/0.5/1`；
`speed` 必须是 `0.7..1.2` 的有限数字，布尔值或字符串不能替代数字。
Multilingual v2/Flash 默认发送 `use_speaker_boost=true`；v3 默认省略该字段以保持已验证请求。
显式布尔值会原样发送并单独参与缓存，但 v3 的该覆盖项未由上述短测验证。
不接受未知 `voice_settings` 字段，也不发送表演说明或自然语言指令。

保持已验证的提供方、模型和音色；未知 provider 报错，不自动切换。
豆包先经适配器转为 PCM，再做后处理；MiMo 使用返回的 WAV。
ElevenLabs 使用带时间戳接口返回的 base64 MP3，临时解码为 PCM WAV，成功或失败后均清理临时 MP3。
最终统一应用 `atempo` 和 `loudnorm=I=-20:TP=-2:LRA=11`，输出 44.1 kHz、单声道、16-bit PCM WAV。

## 3. 执行与凭证

在包根目录运行，阶段入口会提供适配器路径及工具目录：

```bash
python3 scripts/run_skill.py video-voiceover voiceover.py \
  --project /path/project.json \
  --plan /path/work/editorial_plan.json \
  --work-dir /path/work
```

也可在本技能目录直接运行 `python3 scripts/voiceover.py`，使用相同三个参数，
并设置 `PYTHONPATH` 为包根的 `scripts` 绝对路径。运行需要 FFmpeg；
AIHub 适配器只在实际使用提供方时导入。

凭证使用 `APP_ID` / `APP_KEY`。设置 `RECAP_ENV_FILE` 时读取指定文件，
否则读取包根 `.env`；已有环境变量优先。显式指定的文件不存在则报错。
不打印密钥，不将外部凭证文件复制进项目，不将请求认证信息写入音频目录。

ElevenLabs 同样使用 `aihub_adapter.credentials()` 与 `BASE`，不需要 `xi-api-key`。
请求为 `POST /v1/text-to-speech/{voice_id}/with-timestamps`，查询参数
`output_format=mp3_44100_128`；AIHub Bearer 认证带 `provider=elevenlabs`、
与正文一致的原生 `model`、每次请求新建的 `cache_task_id`、`timeout=120`、`usage=1`。
正文仅包含 `text`、`model_id`、有效 `voice_settings` 及可选 `language_code`。
不跟随重定向、不自动重试 POST；超时或 HTTP 错误只报告错误类别或状态码，
不输出服务端错误正文、认证信息或完整响应。重跑失败片段可能产生新的计费请求。

适配器函数为
`synthesize(text, output_wav, *, speaker, model="eleven_v3", voice_settings=None, language_code=None) -> Path`，
返回 `.provider.json` 路径。`tts_settings` 使用相同关键字参数（不含正文与输出路径），
返回用于请求与缓存的有效配置，不读取凭证、不发请求。

## 4. 输出、缓存与交接

- `editorial_tts/`：原始音频、后处理音频、缓存校验信息及提供方记录。
- `audio_catalog.json`：按旁白 ID 保存绝对音频路径、实测秒数、正文、音色、
  provider、model 和 `post_tempo`。
- ElevenLabs 条目还包含有效 `voice_settings`、`language_code`，提供方文件存在时包含
  `provider_metadata_path`、`alignment_clock="raw_audio"` 和 `alignment_time_mapping`。
  映射为 `raw_audio → ready_audio`，`scale=1/post_tempo`、`offset_seconds=0`、`exact=false`。
  这是后处理倍率的名义映射，未经后处理音频强制对齐；原始时间戳不会伪装成最终音频时间戳。
- `audio_durations.json`：`{旁白ID: 实测秒数}`，供剪辑阶段编译时间线。

缓存绑定正文、音色、目标语速、原生语速、提供方、模型及版本，复用前检查最终音频 SHA-256。
ElevenLabs 额外绑定全部有效原生设置、语言码、端点与音频格式，并核对提供方记录中的原始 WAV 指纹；文件对不完整或指纹不符时重新生成。MiMo/豆包历史缓存身份保持原样。
修改正文或声音配置后重新执行；音频被改动时重新生成。时长读取最终 WAV 的帧数，不能用字数估算。

ElevenLabs `.provider.json` 仅保存正文、模型/音色/有效参数、音频格式与实测原始时长、
`alignment`、`normalized_alignment`、允许的数值用量计数和请求 ID。
音频与记录保存失败时恢复先前文件，`audio_sha256` 用于识别中断写入的不匹配文件对。时间单位为秒，`clock="raw_audio"`。两套对齐均可缺失或为 `null`；提供数组时必须等长、
开始/结束时间有限且各自单调、每项 `0 <= start <= end`，上限为原始 WAV 时长加 0.15 秒容差。
无效对齐或音频会拒绝发布本次 WAV/记录，不保留完整 API 响应或 base64 音频。

包根渲染桥接入口 `editorial_render.py` 显式复用本阶段的 `prepare_audio`；
其 `--audio-only` 行为保留。若配音超出已确认的证据窗口，返回写稿或剪辑阶段调整，
再重新配音和编译；不通过截尾、自动移位或下游额外加速解决。
