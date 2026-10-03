# Hermes+ GTD 报告收件箱

2026-10-03 迁自 hermes-plus 仓库 server/，此前的历史见该仓库提交 [9d7844e](https://github.com/birderyu/hermes-plus/commit/9d7844e)、[f2bc57d](https://github.com/birderyu/hermes-plus/commit/f2bc57d)。

独立插件负责保存定时任务报告、向唯一 API 会话提供只读上下文，以及可选的 Apple 推送。Apple 提醒事项和日历仍是 GTD 的权威数据源。插件不创建、重跑或修改 GTD 定时任务，不修改提醒事项，不直接写 Hermes 的模型 transcript。

2026-10-03：已部署到 Mac mini 的运行网关，`hermes_plus`、现有 API 与 Photon 均连接正常。今早已完成的真实 GTD 报告经过宿主持久队列进入收件箱，重复投递仍只有一份，API 读取正文校验一致。**APNs 密钥与推送签名尚未配置，真实锁屏通知未验收；四个原任务仍投递 Photon/iMessage。** 支持报告同步的普通开发签名版本已覆盖安装到 iPhone，前台同步仍待解锁验收。

## 投递合同

- 平台名 `hermes_plus`，唯一投递目标 `hermes_plus:main`。
- 配置文件为当前 Hermes home 的 `hermes-plus/inbox.json`。`session_id` 使用已有 Hermes+ 会话根 ID；只允许配置列出的 GTD job ID。
- 当前 Hermes 的 live adapter metadata 只有 `job_id`，缺少 `execution_id`。随附 `compatibility.py` 对 `cron/scheduler_delivery.py` 的一条字典声明补充已有执行编号。不会自行加载、猴子补丁或运行时修改核心；上游结构漂移时拒绝修改。保留原始文件备份后才能应用。
- `job_id + execution_id + 会话根 ID` 唯一标识一份报告。同一次投递重试返回原报告 UUID；正文冲突拒绝覆盖。内容相同但执行编号不同的报告分别保留。
- 缺少执行编号的投递明确失败，不用时间窗口或当前活动任务猜测身份。进程外独立 sender 明确拒绝投递；需要运行中的网关 live adapter。当前 Hermes 的 detached cron worker 通过自身 delivery queue 回到 live gateway。
- 原调度器的固定包装标题和管理脚注经过精确匹配后去除，报告本身保持原文。

示例配置（会话 ID 从既有 `hermes-plus/conversation.json` 读取；不要创建新会话）：

```json
{
  "session_id": "EXISTING_API_SESSION_ID",
  "jobs": {
    "d83281b75902": "今日安排",
    "8211bb91402a": "今日安排",
    "12d6987e974e": "清理收件箱",
    "f9a5dd70ea34": "每周回顾"
  }
}
```

## 持久化与通知

数据保存在当前 Hermes home 的 `plugin-data/hermes-plus-inbox/inbox.sqlite`，文件权限 0600。它保存报告副本、设备推送登记和通知尝试；不建立另一份待办数据库。报告先提交 SQLite，再由 API 服务器的异步队列发送推送。

每个设备独立登记和撤销，与自动位置授权无关。开启通知不补发旧报告的锁屏提醒；旧报告仍可同步。登记和 token 轮换具有代次，旧 APNs 回调不能撤销新的 token。网络重试保留报告 UUID，通知队列最多尝试五次并在一天后过期；APNs 的 collapse ID 使用报告 UUID。APNs 接受、界面打开报告和正文保存是不同状态。网络结果不确定时，不能保证系统横幅绝对只出现一次；聊天报告仍只有一份。

推送只有固定文案“有新的 GTD 报告，打开查看。”和报告 UUID，不带事项、标题、会话 ID 或密钥。点击通知后由已认证 API 获取正文。前台已同步同一报告时不额外弹横幅；同步失败时保留系统提醒。无推送配置、权限关闭或手机离线时，报告仍保存；应用回到前台即补同步，并在前台每 30 秒检查。

沿用 [APNs 配置说明](../hermes-plus-location/APNS.md) 中的四个服务端变量。推送私钥必须保存在仓库外，配置只保存绝对路径。iOS 必须使用支持 Push Notifications 的显式 App ID 和描述文件，签名添加 `App/HermesPlus.entitlements`，设置 `HERMES_PLUS_APNS_ENVIRONMENT=development` 或 `production`。付费开发者账户、可安装 App 与已启用推送是不同条件。

## HTTP 与会话上下文

以下所有接口先校验已有 API owner key，且绑定到已配置的会话压缩/续接链。其他会话返回 403；未配置返回 503。不提供公开写入报告的 HTTP 接口。

| 请求 | 用途 |
| --- | --- |
| `GET /v1/hermes-plus/inbox?session_id=...` | 协议版本与推送配置状态 |
| `GET /v1/hermes-plus/inbox/reports?session_id=...&after=...` | 按递增 sequence 分页，每页最多 100 条 |
| `GET /v1/hermes-plus/inbox/reports/{id}?session_id=...` | 按通知 ID 读取正式报告 |
| `PUT /v1/hermes-plus/inbox/devices/{id}` | owner 登记 token、环境和会话 |
| `DELETE /v1/hermes-plus/inbox/devices/{id}?session_id=...` | 撤销并取消未发送的通知 |
| `POST /v1/hermes-plus/inbox/reports/{id}/read` | 客户端明确打开报告后记录已读 |

`pre_llm_call` 为该 API 会话注入最近六份报告，限制上下文大小；较长及更早报告通过只读 `get_gtd_reports` 获取。工具遵循宿主注入的 session ID，并再次验证续接链。报告被明确标为资料，不构成用户新指令、事项修改授权或用户确认；序号不明时应澄清，操作前应核验实时事项。这是模型行为合同，仍需真实 GTD 对话验收。

## 部署与回滚

下列是完整部署流程。2026-10-03 已完成插件部署、兼容修改、空闲网关备份及排空重启；配置 Apple 推送、推送签名、手机验收与任务切换仍待完成。当天部署证据和回滚备份位置见 [本轮 QA](https://github.com/birderyu/hermes-plus/blob/main/native/qa/gtd-inbox-2026-10-01.md#2026-10-03-部署进展)。

1. 检查运行中的网关和活动任务，在授权的空闲维护窗口备份当前插件、网关配置、`cron/jobs.json`、`hermes-plus/conversation.json` 与待修改的调度源文件。数据库在线备份使用 SQLite backup，不能直接复制后再覆盖在线数据库。
2. 部署 `plugin.yaml`、`__init__.py`、`store.py`、`apns.py` 到 `~/.hermes/plugins/hermes-plus-inbox/`；配置上述会话映射。运行 `hermes plugins doctor <目录> --ci`，通过后启用插件，在网关配置中启用 `platforms.hermes_plus.enabled: true`。
3. 先运行 `python compatibility.py <Hermes源码目录>` 检查。经审阅后加 `--apply`，保存 `.py.hermes-plus-inbox.bak`。如备份已存在，不覆盖；先确认上次操作状态。检查 Hermes 升级是否已包含等价 metadata 再决定是否重应用。
4. 配置 Apple 推送和正确的开发签名。排空网关，使用既有 launchd 服务支持的 `SIGUSR1` 维护重启；验证现有 API 和 Photon 均健康，保留四个原任务的投递目标。
5. 安装同 Bundle ID 的新版 App，保留 Keychain、缓存和草稿。用隔离内容验证投递、关闭通知补同步、锁屏通知、点击定位和重启去重；再完成一次用户授权的只读 GTD 报告与后续对话验收。
6. 验收完成后只更新四个原任务：`deliver` 改为 `hermes_plus:main`，显式 `attach_to_session: false`，提示词中的 iMessage 格式要求改为 Hermes+ 纯文本报告。使用 Hermes 的加锁任务更新接口；保存原字段用于回滚。不改变 ID、时间、Skill、业务规则，不复制新任务。
7. 出现问题先恢复四个原任务的原投递字段和格式提示，确认 Photon 健康；必要时在下一次空闲窗口停用插件、恢复兼容补丁并重启。保留 inbox 数据用于排查，不能用旧数据库覆盖运行中状态。

## 验证命令

以下命令从 Hermes Agent 仓库根目录执行，`python3` 应指向 Python 3.11 或更新版本；本次迁移对照使用 Python 3.12。若系统默认仍是 Python 3.9，请将命令中的 `python3` 替换为已安装的 `python3.12`。

```sh
python3 -m unittest discover -s hermes-plugins/hermes-plus-inbox -p 'test_*.py' -v
python3 hermes-plugins/hermes-plus-inbox/compatibility.py /path/to/hermes-agent
# 用 Hermes 的 venv 和源码搜索路径运行，脚本自行创建临时 HERMES_HOME。
PYTHONPATH=/path/to/hermes-agent /path/to/hermes-agent/venv/bin/python hermes-plugins/hermes-plus-inbox/integration_sandbox.py
# 原生客户端检查在 hermes-plus 仓库运行。
bash /path/to/hermes-plus/native/scripts/check.sh --ios
```

`integration_sandbox.py` 仅使用临时数据和 loopback HTTP，不运行真实 GTD、推送、提醒事项或在线网关。完整验收与原生截图见 [本轮 QA](https://github.com/birderyu/hermes-plus/blob/main/native/qa/gtd-inbox-2026-10-01.md)。
