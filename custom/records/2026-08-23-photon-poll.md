# 2026-08-23 至 08-24 Photon 原生投票修复

## 空标题投票（`1e8719d74e` fix(photon): accept empty iMessage poll titles）

**现象**：Hermes 调用带选项的 `clarify` 后，Photon 能在 iMessage 里创建原生投票，但用户点选项后结果回不到 Hermes。日志：

```text
[spectrum.imessage.poll] ERROR failed to cache poll
ZodError: Too small: expected string to have >=1 characters  path: ["title"]
```

**根因**：Photon 的投票变更事件可能带空标题；`@spectrum-ts/imessage` 在 `toCachedPoll()` 里把空值交给 `asPoll()`，而 Spectrum 的 Poll 结构要求标题至少一个字符。错误发生在 SDK 的投票缓存层，早于 Hermes 适配器处理 `poll_option`，所以只改适配器修不了。

**修复**：sidecar 增加补丁脚本，SDK 收到空标题时只在内部缓存里用占位标题 `Poll`，iMessage 显示的问题、选项和用户点的选项文字都不变。

- 文件：`plugins/platforms/photon/sidecar/patch-spectrum-poll-empty-title.mjs`（及 `.test.mjs`）、`sidecar/index.mjs`、`sidecar/package.json`。
- `postinstall` 在依赖安装或更新后重新打补丁；sidecar 启动时加载 SDK 前再检查一次。
- 补丁带标记、可重复执行；只匹配已知的代码结构，SDK 结构变了会明确报错，不会静默改错位置。

**验证**：JavaScript 行为测试 3 项、Photon Python 测试 4 项通过；`postinstall` 连跑两次确认幂等。真机：二选一投票点「通过」后，`clarify` 收到 `user_response: "通过"`，Hermes 继续回复，没有再出现空标题 ZodError。

## 已读回执循环（`5ebc985239` fix(photon): drop receipt loops and repeat votes）

位置卡片相关提交把 sidecar 的 `spectrum-ts` 升到 12.8.0 后，新版开始把 iMessage 已读回执作为 `read` 内容送进适配器，旧适配器把它当成用户消息，形成「回复 → 已读 → 再回复」的循环。修复：`read` 在适配器入口静默丢弃。

这个提交还对所有原生投票加了「同一投票、同一发送者只接受首次选择」。真机证明它按设计生效，但范围过宽：破坏了普通投票的改票和多选。次日由下面的提交纠正。

## 投票生命周期收窄到 clarify（`e5b106a3f3` fix(photon): scope native poll lifecycle to clarify）

- 删除全局「首次投票锁定」，普通原生投票保持 Spectrum 的选择与改票语义。
- 只记录由 `clarify` 创建的投票，把投票消息 ID 和对应的 `clarify_id` 关联。
- 单选 `clarify` 的有效选择直接解除等待；投票关闭后的改票归属原交互，静默吸收，不进入普通消息。
- `multi_select=true` 时可以连续勾选、取消，额外显示「✅ 完成选择」，点击后一次把当前选择数组交回 `clarify`。
- 重启前或非 Hermes 发起、无法关联的投票事件静默忽略。

**验证**：相关 Python 测试 99 项通过。真机多选：依次勾选「甲」「丙」、取消「甲」、勾选「乙」、点「✅ 完成选择」，`clarify` 收到 `["乙", "丙"]`，只回复一次；中间事件没有进入普通消息通道。

## 升级时要做

每次跟官方升级后，运行 sidecar JavaScript 测试，并完成一次真实的原生投票创建与点击回传。上游正式兼容空标题后，先确认行为测试仍通过，再单独移除空标题补丁。

## 后续

- 2026-10-11 升级到 v0.21.6：上游已自行丢弃 `read`/`read_receipt`，回执补丁移除；生命周期补丁在上游新代码上重新实现；sidecar 回到上游的 `spectrum-ts 12.7.0`，空标题补丁保留。见 [v0.21.6 升级](2026-10-11-v0.21.6.md)。
