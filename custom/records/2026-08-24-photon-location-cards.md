# 2026-08-23 至 08-24 Photon 位置卡片

## 结论

iMessage / Find My 位置卡片能被识别，但 Photon 的 Spectrum Cloud 共享线路不返回可用的地址、坐标或 `miniApp` 内容，还把发送者的联系句柄放进 `address` 字段。`custom` 做了安全规避：识别位置卡片，没有真实位置时请用户补发地图链接、地址或坐标，不再把手机号或邮箱当成地址。上游问题已提交：[photon-hq/advanced-imessage-ts#53](https://github.com/photon-hq/advanced-imessage-ts/issues/53)（截至 2026-10-11 仍为 Open）。

## 已核验现象（spectrum-ts 12.8.0、@photon-ai/advanced-imessage 2.1.0、gRPC、Spectrum Cloud 共享线路）

- `balloonBundleId` 能识别为 `com.apple.findmy.FindMyMessagesApp`。
- 入站消息 `miniApp` 为空，也没有带地址或坐标的文字、附件。
- `locations.get(sender)` 返回的 `address` 等于发送者联系句柄；`longAddress`、`shortAddress`、`name`、经纬度、`locationTimestamp` 都缺失；`isLocatingInProgress` 一直为 `true`。
- 40 秒有界观察里，`locations.watch(sender)` 没有任何更新。

以上只做字段存在性与类型检查，没有保存原始消息载荷、手机号、邮箱或实际位置。

## 补丁（当时的 SHA）

| 提交 | 作用 |
| --- | --- |
| `83e821db3a` | 识别 iMessage 位置卡片 |
| `79d616e518` | 解码地图卡片内容；sidecar `spectrum-ts` 升到 12.8.0；新增 mini-app 入站补丁 |
| `50c050bf97` | 对 Find My 卡片查询 Photon Location snapshot |
| `a4db471c83` | 容忍位置快照的短暂延迟 |
| `817e44dfe1` | 重试瞬时查询失败 |
| `84002b22ff` | 保持重试计时器有效 |
| `e509207e26` | 容忍位置路由预热 |
| `1441aaf550` | 定位中保留确实可用的地理地址 |
| `8a8b9d7bce` | 排除与发送者联系句柄相同的伪地址 |

`79d616e518` 把 Spectrum 升到 12.8 后，暴露出已读回执循环和 `reply` 包装两个问题，分别见 [Photon 原生投票](2026-08-23-photon-poll.md) 和 [回复消息解析](2026-08-24-photon-reply.md)。

## 后续

2026-10-11 升级到 v0.21.6 时整组移除：Photon Cloud 一直没有返回位置数据，这组补丁实际只提供一句兜底提示；而位置已改由 Ollo App 提供（`hermes-plugins/ollo-location`），这组补丁靠安装后改写 Spectrum 的依赖代码，每次升级都要护着。移除后 sidecar 回到上游的 `spectrum-ts 12.7.0`。上游 #53 若修复、又确实需要 iMessage 位置，再按新的 SDK 能力重新实现。见 [v0.21.6 升级](2026-10-11-v0.21.6.md)。
