---
name: video-voiceover
user-invocable: false
description: >
 用于已确认旁白的独立配音、音色或语速调整，以及剪辑前取得真实音频时长；支持 AIHub MiMo 与豆包语音。
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
- `tempo` 必须是 `0.5..2` 的有限数字。请求保持原生语速 `+0%`，由本地处理应用目标语速。

保持已验证的提供方、模型和音色；未知 provider 报错，不自动切换。
豆包先经适配器转为 PCM，再做后处理；MiMo 使用返回的 WAV。
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

## 4. 输出、缓存与交接

- `editorial_tts/`：原始音频、后处理音频、缓存校验信息及提供方记录。
- `audio_catalog.json`：按旁白 ID 保存绝对音频路径、实测秒数、正文、音色、
  provider、model 和 `post_tempo`。
- `audio_durations.json`：`{旁白ID: 实测秒数}`，供剪辑阶段编译时间线。

缓存绑定正文、音色、目标语速、原生语速、提供方、模型及版本，复用前检查最终音频 SHA-256。
修改正文或声音配置后重新执行；音频被改动时重新生成。时长读取最终 WAV 的帧数，不能用字数估算。

包根渲染桥接入口 `editorial_render.py` 显式复用本阶段的 `prepare_audio`；
其 `--audio-only` 行为保留。若配音超出已确认的证据窗口，返回写稿或剪辑阶段调整，
再重新配音和编译；不通过截尾、自动移位或下游额外加速解决。
