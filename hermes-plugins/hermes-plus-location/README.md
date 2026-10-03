# Hermes+ 位置插件

2026-10-03 迁自 hermes-plus 仓库 server/，此前的历史见该仓库提交 [9d7844e](https://github.com/birderyu/hermes-plus/commit/9d7844e)、[f2bc57d](https://github.com/birderyu/hermes-plus/commit/f2bc57d)。

独立 Hermes 插件，包含原有显式限时共享和新增的按需设备位置工具。复用客户端 HTTPS 入口，不修改 Hermes core，不新增公网监听。仅默认 Profile 的原生 API 路径，暂不提供 `/p/<profile>` 镜像路由。

## 按需位置

用户在 iPhone 开启一次设备授权后，`get_user_location` 可根据当前任务需要读取位置。服务端先查符合要求的最新观测；不足时持久化短请求，客户端独立于聊天任务领取请求、定位并回传，工具最多等待 15 秒。此通道不生成用户聊天消息，也不会因为聊天正在回复而阻塞；工具返回的位置仍可能进入模型上下文及工具历史。

系统定位权限、可撤销设备授权、单次请求期限、坐标 TTL 分别管理。持久授权不自动延长单次请求或坐标的新鲜度。坐标超过 120 秒不会用于当前定位；`max_age_seconds=0` 要求采集时间不早于该次请求的 `requested_at`，其余值限于 1–120 秒。工具返回来源设备、WGS84 坐标、采集时间、精度、数据年龄与是否命中缓存。

同一授权会话只有一个 iPhone 时使用该设备；没有 iPhone 时可使用唯一 Watch。多台 iPhone 返回 `device_selection_required`，不按最近上传时间猜测随身设备；iPad/Mac 不自动代表用户。设备选择 UI 与跨渠道 owner 身份绑定属于后续扩展。

工具仅接受 Hermes 注入的 runtime `session_id`，不接受模型提供的会话或设备标识。必须先由可信的 `pre_llm_call(platform='api_server')` 在该会话内确认 API 上下文；通过 `resolve_resume_session_id` 跟随同一会话压缩续接。其他渠道、其他会话、未授权设备拒绝读取。新设备授权存在时 hook 只声明按需工具可用，不注入坐标；未使用新授权的旧限时共享保持原行为。

## HTTP 合同

基础路径为 `/v1/hermes-plus/location`。所有响应为 JSON。`Point` 为 `{latitude,longitude,accuracy,timestamp}`，坐标 WGS84、精度米、时间 Unix 秒。

Owner Bearer key 仅用于能力探测、登记/重新授权和撤销：

| 方法/路径 | 请求 | 响应 |
| --- | --- | --- |
| `GET /` | 无 | `{version:1,durations:[120,900,3600],device_protocol_version:1,on_demand:true}` |
| `POST /devices` | `{device_id,session_id,display_name,platform}` | `{device_id,grant_id,device_token,authorized:true}` |
| `DELETE /devices/{device_id}` | 无 | `{revoked:true}` |

`device_id`、`grant_id` 是 UUID，设备 ID 规范化为小写。`session_id` 必须已存在；`display_name` 1–128 字符且无控制字符，`platform` 为 `iOS/watchOS/iPadOS/macOS`。重新授权轮换随机设备凭据与 grant，清除旧设备缓存/推送登记并终止该设备旧请求。服务端只持久化凭据的 SHA-256 摘要。客户端必须先把凭据存入设备 Keychain 才开始采集；存储失败应使用 owner key 撤销。

设备路径只接受登记所得 device Bearer token，不需要在设备轮询中发送 owner key：

| 方法/路径 | 请求 | 响应 |
| --- | --- | --- |
| `GET /device/state` | 无 | `{device_id,grant_id,authorized:true,latest:Point|null,pending_requests:[Request]}` |
| `PUT /device/observation` | `{grant_id,sequence,point,request_id?}` | `{accepted:true}` |
| `POST /device/results` | `{grant_id,request_id,status}` | `{accepted:true}` |
| `POST /device/push` | `{grant_id,apns_token,environment}` | `{accepted:true}` |

`Request` 是 `{id,requested_at,expires_at,max_age_seconds}`。`sequence` 是每个 grant 内递增的正整数；乱序/重复上传不会覆盖较新点，已完成请求的重复回执不会重复执行。成功按请求回传携带 `request_id`，平时后台同步最新点可省略。失败 `status` 可为 `permission_denied/location_disabled/timeout/background_unavailable/unavailable/cancelled`。推送环境为 `sandbox/production`；推送 token 是偶数长度十六进制字符串，不假定固定 64 字符。

无效/已撤销设备凭据或 grant 返回 403；未知、过期或已结束请求返回 410（不会撤销持久授权）。输入不合法返回 400，未知登记会话 404，过大请求 413。设备 token 不可访问 owner 接口；持有 owner key 的客户端不能把该 key 当设备 token。撤销清除最新点、设备凭据与推送 token，终止未完成请求，并阻止迟到上传。离线关闭需要客户端先停止本机采集，再持久保存撤销意图，联网后重试 owner 撤销。

可选 APNs 只提示客户端拉取已持久化请求，不承载坐标/凭据，不证明设备已被唤醒。未配置时前台轮询仍可用，见 [APNs 配置与边界](APNS.md)。

## 原限时共享兼容

- `POST /`：`{device_id,session_id,duration}`，duration 为 120/900/3600，返回 `{id,expires_at}`。
- `PUT /{id}`：`{sequence,point}`。
- `DELETE /{id}`：终止共享并清空该共享坐标；重复停止安全，迟到上传返回 410。

限时共享与持久设备授权互不撤销。每台设备的新限时共享仍替换其旧限时共享，上传不能续期。旧流程仅向同一 API 会话（含压缩续接）注入不超过 120 秒的位置；上传不调用模型或写用户 transcript。停止无法撤回已经进入模型上下文或聊天记录的数据。

数据库位于当前 Hermes home 的 `plugin-data/hermes-plus-location/latest.sqlite`，权限 0600。仅保存每个设备/限时共享的最新点；独立请求结果短暂保留用于重试，坐标过期后清除，不建立轨迹档案。授权状态可跨服务重启恢复。

## SDK 与部署

部署文件为 `plugin.yaml`、`__init__.py`、`apns.py`。安装到 `~/.hermes/plugins/hermes-plus-location/`，使用 Hermes `plugins doctor` 验证，启用插件并在获授权的维护窗口重启网关。2026-09-28 用户明确授权后，已在 Mac mini 完成插件 1.1.0 部署及一次空闲排空重启；后续服务操作仍应遵循项目授权范围。

工具使用 `ctx.register_tool(..., is_async=True)`，handler 返回 JSON 字符串；内部通过 `asyncio.to_thread` 执行同步 SQLite 和短等待，APNs 始终调度到 API 服务器事件循环。HTTP 路由使用 `ctx.register_platform_handler('api_server', wire)` 并分别校验 owner 与设备凭据，不改写 Hermes 原有认证函数。注意 Hermes 的 `register_middleware` 是 Agent 执行中间件，不是 HTTP 认证扩展点。

接口按官方 SDK 源码核对：

- [工具注册与原生 API 扩展](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/)
- [registry 的 handler 参数与返回值](https://github.com/NousResearch/hermes-agent/blob/main/tools/registry.py)
- [runtime session 注入及 async bridge](https://github.com/NousResearch/hermes-agent/blob/main/model_tools.py)
- [API 服务路由与 handler 认证](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/api_server.py)

Mac mini 实际 Hermes `20e0174ee037be7c0d68132e1d0a2cf51ce73e8a` 的 SDK、路由接入与认证已现场核对；最终 `plugins doctor --ci` 退出 0、无警告。重启后 `/v1/toolsets` 确认 `hermes_plus_location` 已启用，包含 `get_user_location`。工具 runtime 不保证提供 tool_call_id，因此核心依靠同设备/同 grant/同会话的等价未完成请求合并和请求 ID 的幂等结果；可用时额外利用 runtime call ID 去重。

本次部署前旧插件与 SQLite 一致性备份位于 Mac mini `~/.hermes/backups/hermes-plus-location-20260928T225546/`。回退旧版时，在授权的空闲窗口恢复其中插件文件并排空重启，保留原有限时共享；不要无条件恢复旧数据库而覆盖后续有效写入。若要完全关闭所有位置功能，可禁用插件后在授权维护窗口重启。坐标过期独立于手机停止通知；卸载不会撤回已经写入模型上下文的数据。

## 验证

以下命令从 Hermes Agent 仓库根目录执行，`python3` 应指向 Python 3.11 或更新版本；本次迁移对照使用 Python 3.12。若系统默认仍是 Python 3.9，请将命令中的 `python3` 替换为已安装的 `python3.12`。

```
python3 -m unittest discover -s hermes-plugins/hermes-plus-location -p 'test_*.py'
```

安装 aiohttp 的隔离 Python 环境同时运行真实 loopback HTTP 合同测试；无 aiohttp 时这三项明确跳过。测试使用临时数据库、模拟坐标、mock owner adapter 与模拟 APNs，不访问真实位置、聊天、模型或线上服务器。覆盖租约兼容、授权/凭据轮换、撤销后的迟到上传、会话隔离及压缩续接、缓存新鲜度、max_age=0、顺序与幂等、失败/超时、多设备选择、异步工具回传以及 HTTP JSON/鉴权合同。

2026-09-17 原限时共享曾在 Mac mini 使用临时 API 会话与模拟坐标验证。该历史验收不代表本次按需工具、真实 GPS、后台唤醒、锁屏、省电/网络切换或 Watch 已通过真机验收。

2026-09-28 新版已在 Mac mini 通过独立临时会话的线上登记、设备回传、owner/device 凭据隔离、撤销后拒绝迟到上传，以及旧限时共享兼容性验收；临时资源已清理、没有产生聊天消息或调用模型。部署后网关最终健康状态正常。2026-09-29 新版已覆盖安装到实体 iPhone 并正常启动；APNs 尚未配置，真实定位与后台能力尚未验收。完整证据与剩余真机项目见 [自动位置验收](https://github.com/birderyu/hermes-plus/blob/main/native/qa/automatic-location-2026-09-28.md)。
