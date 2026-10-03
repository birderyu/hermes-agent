# 可选 APNs 后台提示

APNs 只提示 iPhone 领取服务端已经保存的 `location.read` 请求。`accepted` 仅表示 Apple 接受该提示；请求完成以手机经过设备认证回传的结果为准。未配置、依赖缺失、发送失败、系统未送达或节流，都不删除请求、不撤销位置授权，也不影响前台领取。

## 服务端配置

仅需 APNs 时，在运行 Hermes 的同一 Python 环境安装可选依赖：

```sh
python3 -m pip install 'httpx[http2]' 'PyJWT[crypto]'
```

为 Hermes 服务进程配置以下环境变量。`.p8` 保存在仓库之外的私有文件中；不要将文件内容、JWT 或设备 token 写入日志、命令参数、仓库或聊天。

| 环境变量 | 内容 |
| --- | --- |
| `HERMES_PLUS_APNS_KEY_PATH` | APNs `.p8` 文件的绝对路径 |
| `HERMES_PLUS_APNS_KEY_ID` | Apple 的 10 位 Key ID |
| `HERMES_PLUS_APNS_TEAM_ID` | 签名团队的 10 位 Team ID |
| `HERMES_PLUS_APNS_TOPIC` | 与推送描述文件一致的 iOS Bundle ID |

`APNsProvider.status()` 的 `ready` 仅确认配置形式完整；实际发送仍可能返回 `dependencies_unavailable`、`credentials_unavailable` 或 Apple 拒绝状态。未配置返回 `not_configured`。修改密钥或团队后重启服务以重建连接和 JWT 缓存。

设备登记的环境仅接受 `sandbox` / `production`，分别使用 Apple 的固定 HTTPS 地址；不允许自定义推送地址。iOS 的 `aps-environment=development` 对应 `sandbox`。环境与 topic 必须和当前安装的签名 App 一致。

## iOS 签名

共享工程默认不启用推送权限。需要推送时，使用已支持 Push Notifications 的 App ID 和描述文件，在 iOS 签名构建中显式指定：

```text
CODE_SIGN_ENTITLEMENTS=App/HermesPlus.entitlements
HERMES_PLUS_APNS_ENVIRONMENT=development
```

正式分发使用 `production`。运行时依据真实签名中的 `aps-environment` 决定是否注册远程通知；普通未启用推送的构建继续使用前台请求通道与获授权的后台位置更新。本文件不会修改 Apple 开发账号、创建密钥或配置描述文件。

## 行为与验证边界

- APNs 使用 HTTP/2、`background`、优先级 `5` 和 `content-available: 1`；payload 只有能力名和请求编号，不包含位置或设备凭据。
- 同一 token / 环境在当前服务进程中至少间隔 20 分钟发一次提示；多个请求通过同一 collapse ID 合并。请求仍由服务端队列管理。
- 单次网络尝试总等待不超过 5 秒及请求剩余期限。Provider JWT 使用 ES256，复用 50 分钟后更新。
- Apple 判定 token 失效时，只能清除仍匹配本次发送、且没有更新登记的 token；不会关闭用户的位置授权。
- `test_apns.py` 使用模拟网络和模拟签名，覆盖配置、JWT 字段、请求头、环境、隐私 payload、节流、失效 token、超时与错误处理。没有调用真实 APNs、读取真实密钥或证明锁屏唤醒成功。

官方依据：[APNs 请求](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns)、[后台通知与发送频率](https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app)、[JWT 认证](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns)。
