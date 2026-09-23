# 模型与配置说明

## 本包默认

- 理解视觉、全局索引：Gemini3.8 Flash，`low`。
- 原片ASR：豆包Seed ASR2.0。
- 第二阶段模型审稿：豆包Seed2.1 Pro260628（已有旧站路线）。
- 新项目默认配音：MiMo-v2.5-TTS，茉莉，原速。

用户近期让最新版Seed260915参与独立对照，最终选择继续用Gemini理解。该对照不自动改变审稿或语音服务；两边的`low`也不是相同计算预算。

## 凭证

私有配置格式是`NAME=value`，不是可执行shell：

```dotenv
AIHUB_API_KEY=
APP_ID=
APP_KEY=
```

新站Key给Gemini；旧APP_ID/APP_KEY给ASR、审稿、MiMo/豆包/ElevenLabs TTS。ElevenLabs 通过 AIHub 透传，不使用 `xi-api-key`。可复制`.env.example`为`.env`后编辑，也可设置`RECAP_ENV_FILE`指向包外私有文件；进程中已设置的值优先。所有密钥必须来自真实账号，空模板不能发请求。

## Gemini标准协议

`GEMINI_MODEL=gemini-3.8-flash`，`GEMINI_API_URL=http://api.aihub.woa.com/standard/v1/chat/completions`。这依赖可访问AIHub的网络，不是Google直连配置。

`GEMINI_REASONING_EFFORT`可设`low/medium/high`，默认low。默认最低输出预算4096，因为思考占用预算。支持配置超时；不存在关闭思考的none选项。

该网关会忽略`video_url`，适配器将视频data URI送入`image_url`；图片、文本原格式保留。不支持的采样和豆包关闭思考字段会移除，只有最终非空文本进入理解数据。模型与思考设置参与缓存身份。

显式设置`UNDERSTANDING_PROVIDER=aihub-doubao`可回到旧260628理解路线，用于复现历史实验。保持Gemini时无需设置它。

## 配音

项目里的`voice`控制真实渲染，示例：

```json
{"provider":"aihub-mimo","speaker":"茉莉","tempo":1}
```

`provider`可选`aihub-mimo/aihub-doubao/aihub-elevenlabs`。MiMo当前已实测冰糖、茉莉中文女声；豆包沿用各项目的真实speaker ID。原速为1，整体后处理倍率不等于模型自然语速。MiMo当前只发正文，不传网关曾读出来的自然语言控制指令；不声称实现情绪或克隆。

ElevenLabs 对照配置（放在项目 `voice` 中）：

```json
{
  "provider": "aihub-elevenlabs",
  "speaker": "j2FxFb20sd3xlm2pRaM5",
  "model": "eleven_v3",
  "language_code": "zh",
  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0, "speed": 1.1},
  "tempo": 1
}
```

该音色为 Julian，模型使用原生 ID。支持 `eleven_v3`（默认）、`eleven_multilingual_v2`、
`eleven_flash_v2_5`，必须显式选择 `speaker`。v3/Flash 可指定小写两字母 `language_code`，
Multilingual v2 必须省略。原生 `speed` 为 `0.7..1.2`，`tempo` 为额外本地倍率 `0.5..2`，
两者都不能用非有限数字或布尔值。稳定性、相似度与风格为 `0..1`，v3 稳定性仅 `0/0.5/1`。
默认原生设置是 `.5/.75/0/1`；v3 默认不发送 `use_speaker_boost`，另外两个模型默认 `true`。
可显式传该布尔参数，v3 覆盖项尚未实测。有效设置与语言码全部参与 ElevenLabs 缓存身份。

`scripts/elevenlabs_adapter.py` 复用 AIHub `BASE` 和凭证，调用
`/v1/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128`。
Bearer 路由中的原生模型与正文 `model_id` 一致，每次请求使用新的缓存任务 ID，
超时 120 秒并请求 usage；不自动重试 POST。临时 MP3 转为 44.1 kHz 单声道 16-bit PCM WAV。
提供方侧车仅保存允许的参数、用量、请求 ID 与已校验的字符时间戳，时间戳属于原始音频时钟。
目录中的 `alignment_time_mapping` 仅给出本地 `tempo` 的名义缩放，`exact=false`，
真实最终时长仍从后处理 WAV 读取。详情见 `video-voiceover/SKILL.md`。

## 已验证范围

Gemini真实30秒理解、图片/视频输入、ASR、索引与第二阶段准备已跑通；最新四组对照支持继续试用Gemini，但两模型均有过度心理推断。已有《钢铁侠》茉莉版完成首章全配音。离线测试和短测不构成所有影片的稳定质量保证。

主任务反馈 Eleven v3 + Julian 的中文开场短测已返回 HTTP 200、MP3、69 个字符的时间戳和约 15.04 秒音频；
该短测使用 `language_code=zh`、原生 `speed=1.1`，未发送 speaker boost。
完整《功夫》Part1 的生成与听审由主任务处理，不将短测等同于全章验收。
