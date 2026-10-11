# 2026-08-26 Photon iMessage 语音链路

## 现象与原因

| 现象 | 原因 |
| --- | --- |
| 中文语音识别成英语或乱码 | STT 没固定语言；改为 `stt.language: zh`（运行配置） |
| TTS 合成失败 | Edge TTS 需要可用的中文音色；iMessage 语音转码依赖本机 `ffmpeg`（Homebrew 安装） |
| iMessage 语音气泡显示「0 秒」 | sidecar 已把内容转码为 M4A，但上传文件名还是 `.mp3`，内容与扩展名不一致 |
| 语音下面出现和语音相同的文字 | iMessage 自己生成的音频转写，不是 Hermes 复述 |
| 语音过一阵消失 | Apple「音频消息过期」设置，与 Hermes 无关 |

运行配置（STT 语言、TTS 音色 `zh-CN-XiaoxiaoNeural`、ffmpeg）在 Hermes 数据目录和本机，不在本仓库。

## 补丁（`8c847f364d` fix(photon): align voice attachment filename with m4a）

- `plugins/platforms/photon/sidecar/send-format.mjs`：新增 `normalizeVoiceAttachmentName(path, name)`，把语音附件名扩展名统一为 `.m4a`。
- `sidecar/index.mjs`：发送 `kind === "voice"` 附件时调用它。
- `sidecar/send-format.test.mjs`：覆盖 `.mp3`、`.wav` 和没有名字时都生成 `.m4a`。

## 验证

sidecar JavaScript 13 项、Photon 入站/出站媒体 Python 17 项通过；网关重启后 Photon 已连接；真实 sidecar 发送语音返回有效消息 ID。「0 秒」在 iMessage 客户端是否消失没有单独记录，仍待复核。

## 升级时要做

确认本补丁在变基后保留，重跑语音相关测试；换机器后先装 `ffmpeg`，再分别验证 STT、TTS 和真实语音气泡。上游截至 v0.21.6 仍没有这项修复。
