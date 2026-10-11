# 2026-08-24 Photon iMessage 回复消息解析

## 现象与根因

用户在 iMessage 里用「回复」引用 Hermes 的一条消息再输入文字，Hermes 收到的是：

```text
[Photon content type not handled: reply]
```

sidecar 的 `spectrum-ts` 升到 12.8.0 后，线程回复被包装成 `reply { content, target }`；旧 sidecar 没有展开 `reply`，Python 适配器走到未知类型回退，把占位文字交给了模型。

## 修复（`793684863c` fix(photon): decode iMessage reply messages）

- sidecar 新增可单独测试的 `reply-content.mjs`，递归规范化 `reply.content`；保留被回复消息的 ID、方向和不超过 2000 字符的文字预览。
- 适配器在分派前展开 `reply`，复用原有文字、附件、链接、组合消息的处理；把目标消息写进 `MessageEvent.reply_to_*`。
- 目标消息取不到时仍投递用户输入；没有内部内容的畸形 `reply` 静默丢弃。

## 验证

sidecar JavaScript 12 项、完整 Photon Python 测试 145 项通过；网关运行新提交，Photon 已连接。真机：对 Hermes 消息使用「回复」后，入站事件带目标消息 ID 和文字上下文，没有再出现 `content type not handled: reply`，Hermes 只回复一次。记录只保存布尔状态和计数，不保存用户原话。

## 后续

上游 `348a68bb4e`（2026-09-26，PR #100663）做了同样的修复，还补上自己发出的消息被回复时的原文回填（`rich_sent_store`），已包含在 v0.21.6。2026-10-11 升级时移除本补丁，改用上游实现。见 [v0.21.6 升级](2026-10-11-v0.21.6.md)。
