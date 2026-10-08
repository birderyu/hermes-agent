# Ollo GTD 报告收件箱

2026-10-03 迁自 hermes-plus 仓库 server/，此前的历史见该仓库提交 [9d7844e](https://github.com/birderyu/hermes-plus/commit/9d7844e)、[f2bc57d](https://github.com/birderyu/hermes-plus/commit/f2bc57d)。

独立插件负责保存定时任务报告、向唯一 API 会话提供只读上下文，以及可选的 Apple 推送。Apple 提醒事项和日历仍是 GTD 的权威数据源。插件不创建、重跑或修改 GTD 定时任务，不修改提醒事项，不直接写 Hermes 的模型 transcript。

本版为改名候选，尚未部署。历史 Hermes+ 验收不代表本版线上或 APNs 真机验收。

## 投递合同

- 平台名 `ollo`，投递目标 `ollo:main`；旧平台 `hermes_plus` 和目标 `hermes_plus:main` 保留为行为相同的别名。兼容期须在网关配置中同时启用两平台。
- 配置文件为当前 Hermes home 的 `ollo/inbox.json`；仅当 `ollo/` 目录不存在时读取旧 `hermes-plus/inbox.json`。`session_id` 使用已有 Ollo App 会话根 ID；只允许配置列出的 GTD job ID。
- 依赖 live adapter metadata 中已有的 `execution_id`。保留 `compatibility.py` 供只读核验；本次不修改核心、不应用或撤回现有 `cron/scheduler_delivery.py` 兼容补丁。运行版本缺少该字段时拒绝投递，上线前必须核验。
- `job_id + execution_id + 会话根 ID` 唯一标识一份报告。同一次投递重试返回原报告 UUID；正文冲突拒绝覆盖。内容相同但执行编号不同的报告分别保留。
- 缺少执行编号的投递明确失败，不用时间窗口或当前活动任务猜测身份。进程外独立 sender 明确拒绝投递；需要运行中的网关 live adapter。当前 Hermes 的 detached cron worker 通过自身 delivery queue 回到 live gateway。
- 原调度器的固定包装标题和管理脚注经过精确匹配后去除，报告本身保持原文，包括末尾 `ollo-mood` 及既有 `hermes-plus-gtd` 系列围栏。

示例配置（会话 ID 从既有 `ollo/conversation.json` 读取；不要创建新会话）：

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

数据保存在当前 Hermes home 的 `plugin-data/ollo-inbox/inbox.sqlite`，文件权限 0600。首次注册在新目录缺失时先备份旧 `plugin-data/hermes-plus-inbox/` 再搬迁；失败记录日志并继续用旧目录，详见统一部署清单。它保存报告副本、设备推送登记和通知尝试；不建立另一份待办数据库。报告先提交 SQLite，再由 API 服务器的异步队列发送推送。

每个设备独立登记和撤销，与自动位置授权无关。开启通知不补发旧报告的锁屏提醒；旧报告仍可同步。登记和 token 轮换具有代次，旧 APNs 回调不能撤销新的 token。网络重试保留报告 UUID，通知队列最多尝试五次并在一天后过期；APNs 的 collapse ID 使用报告 UUID。APNs 接受、界面打开报告和正文保存是不同状态。网络结果不确定时，不能保证系统横幅绝对只出现一次；聊天报告仍只有一份。

推送只有固定文案“有新的 GTD 报告，打开查看。”和报告 UUID，不带事项、标题、会话 ID 或密钥。点击通知后由已认证 API 获取正文。前台已同步同一报告时不额外弹横幅；同步失败时保留系统提醒。无推送配置、权限关闭或手机离线时，报告仍保存；应用回到前台即补同步，并在前台每 30 秒检查。

沿用 [APNs 配置说明](../ollo-location/APNS.md) 中的四个服务端变量。推送私钥必须保存在仓库外，配置只保存绝对路径。iOS 必须使用支持 Push Notifications 的显式 App ID、描述文件和正确的 `aps-environment` entitlement；具体构建参数由 App 工程负责，本次不改。付费开发者账户、可安装 App 与已启用推送是不同条件。

## HTTP 与会话上下文

下表全部路径均保留 `/v1/hermes-plus/inbox/...` 别名，使用同一处理函数、存储和状态码。以下所有接口先校验已有 API owner key，且绑定到已配置的会话压缩/续接链。其他会话返回 403；未配置返回 503。不提供公开写入报告的 HTTP 接口。

| 请求 | 用途 |
| --- | --- |
| `GET /v1/ollo/inbox?session_id=...` | 协议版本与推送配置状态 |
| `GET /v1/ollo/inbox/reports?session_id=...&after=...` | 按递增 sequence 分页，每页最多 100 条 |
| `GET /v1/ollo/inbox/reports/{id}?session_id=...` | 按通知 ID 读取正式报告 |
| `PUT /v1/ollo/inbox/devices/{id}` | owner 登记 token、环境和会话 |
| `DELETE /v1/ollo/inbox/devices/{id}?session_id=...` | 撤销并取消未发送的通知 |
| `POST /v1/ollo/inbox/reports/{id}/read` | 客户端明确打开报告后记录已读 |

`pre_llm_call` 为该 API 会话注入最近六份报告，限制上下文大小；较长及更早报告通过只读 `get_gtd_reports` 获取。工具遵循宿主注入的 session ID，并再次验证续接链。报告被明确标为资料，不构成用户新指令、事项修改授权或用户确认；序号不明时应澄清，操作前应核验实时事项。这是模型行为合同，仍需真实 GTD 对话验收。

## 部署与回滚清单

部署文件：`plugin.yaml`、`__init__.py`、`store.py`、`apns.py`、`migration.py`。安装到当前 Hermes home 的 `plugins/ollo-inbox/`，禁用并归档旧 `hermes-plus-inbox`，不要同时加载。`compatibility.py` 不作为插件自动运行。

完整逐步清单见 [统一部署清单](../README.md#部署清单以下每一步均需用户另行同意)：备份与停止写入 → 更换插件启用名及工具白名单 → 完整复制 `hermes-plus/` 配置到 `ollo/` → 同时启用 `platforms.ollo` 和兼容平台 `platforms.hermes_plus` → 环境变量迁移 → 数据迁移与 doctor → 经授权把现有任务目标改为 `ollo:main` 并更新语气约定 → 排空重启与新旧路径验证。每一步均须用户另行同意。

四个 APNs 新变量为 `OLLO_APNS_KEY_PATH`、`OLLO_APNS_KEY_ID`、`OLLO_APNS_TEAM_ID`、`OLLO_APNS_TOPIC`，未设置的单项读旧 `HERMES_PLUS_APNS_*`；显式空值不会回退。推送主题示例 `com.birderyu.ollo`。推送载荷仍保留旧 `hermes_plus` 键和 `hermes-plus-gtd` thread ID，以兼容旧 App；通知显示名称改为 Ollo。

回滚先排空并停止写入、保存升级后的数据，再恢复旧插件/启用名/配置/任务字段/提示及环境变量，优先将当前新数据目录改回旧名以保留新报告和推送登记；不要用迁移前备份覆盖升级后的数据。若迁移失败仍在旧目录，无需回搬。核心兼容补丁保持原状。

部署后分别请求 `/v1/ollo/inbox` 与 `/v1/hermes-plus/inbox` 的能力、列表、单报告及授权测试，核对新旧路径的报告 UUID/序号/正文完全相同；同 execution ID 经 `ollo:main` 与 `hermes_plus:main` 重试只有一份。设备登记/撤销/已读也都保留旧路径别名，写验收只用授权测试资源。

## 验证命令

从 Hermes Agent 仓库根目录执行：

```sh
scripts/run_tests.sh -j 2 hermes-plugins --file-retries 0
# 只检查既有 cron metadata 兼容性，绝不加 --apply：
python3 hermes-plugins/ollo-inbox/compatibility.py /path/to/hermes-agent
```

允许 loopback 的隔离环境还可运行 `integration_sandbox.py`；脚本自行建立临时 Hermes home，不运行真实 GTD、推送、提醒事项或在线网关。历史客户端验收见 [QA](https://github.com/birderyu/hermes-plus/blob/main/native/qa/gtd-inbox-2026-10-01.md)，不作为本次部署证据。
