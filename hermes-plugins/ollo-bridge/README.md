# Ollo bridge

2026-10-14：初次测试部署见 [10-11 记录](../../custom/records/2026-10-11-ollo-bridge-deploy.md)。用户提供的验收现状及真实任务/真实清单切换步骤见 [正式切换方案](../../custom/records/2026-10-14-ollo-gtd-production-switch.md)。本次只准备工具和文档，未访问运行 home、部署或重启。下文 D/A 阶段的“候选/未部署”和测试计数是历史开发记录，不代表当前线上状态。

唯一的 Hermes 侧薄插件。网关负责报告、设备/位置、APNs、日程业务回执；本插件只适配 HTTP、可信会话、cron 与 Apple 执行。没有报告/位置/回执数据库，不调用旧插件迁移，不导入旧数据。旧 `ollo-inbox`、`ollo-location`、`ollo-conversation` 源码目录保留；切换时停用旧注册项。

合同：Ollo 仓库 `contracts/bridge.openapi.yaml` 0.2.0-draft（含 A5）；离线模型和合成样例见 [contracts](contracts/README.md)、`fixtures/`。当前是未提交实现，设计会话沙箱外 A5 测试为插件 **286 passed / 0 failed**、cron **1485 passed / 11 skipped**（上游既有跳过）；第三轮 N3 字段字节超限分类修复另待复测，不能据此开放生产 schedule。

## 文件清单

| 文件 | 用途 |
| --- | --- |
| `plugin.yaml`、`__init__.py`、`integration.py` | 注册两个平台名、三个工具、独立 HTTP handlers、发现接口与 hooks |
| `config.py`、`protocol.py`、`http_client.py` | Profile 配置、离线严格 schema/字节/日期校验、回环 HTTP 与截止时间 |
| `identity.py`、`authority_identity.py`、`run_context.py` | API 会话/压缩续接验证、canonical/legacy 回合身份、可信请求 UUID 与 run 查询导出、确认回合工具拦截 |
| `reports.py`、`execution.py`、`location.py` | 精确 cron 外壳移除、可信日期、报告投递、最多 15 秒位置轮询 |
| `schedule.py` | wake/capture/capture lookup/items/interpret、claim/finish、确认分组与真实回执 |
| `wake.py`、`cron_lock.py` | 在宿主任务锁中检查暂停、持久 wake 意图与当日去重 |
| `apple.py`、`helper_process.py`、`native/EventKitBridge.swift`、`native/Info.plist` | 固定 JSON 子进程协议、活动 request_id 登记、普通请求 7 秒、换清单 55 秒自退看门狗；批量读取、捕获核对、AppleScript move 与单次 EventKit commit |
| `apple_config.py`、`collector.py`、`scripts/ollo_collect*.py` | 名称对应表与采集范围校验；只读 collect、三个 cron 入口及共用加载器，独立于插件注册与密钥 |
| `scripts/production_switch.py`、`scripts/production_switch_io.py`、`test_production_switch.py` | 正式切换的只读预检、离线备份/切换/回滚及合成状态测试；不操作 Ollo 网关或 Apple |
| `contracts/schema.json`、`contracts/export_schema.py`、`contracts/README.md` | 当前合同的本地引用闭包、维护导出脚本与来源摘要 |
| `fixtures/*.json` | 42 份合成 bridge 合同样例，含 A2 lookup/in_progress 与 A4 failure 报告 |
| `ollo_bridge_test_support.py`、`test_transport.py`、`test_schedule.py`、`test_cron.py`、`test_apple_contract.py`、`test_runtime.py`、`test_helper_process.py` | 临时 home、HTTP/模型/Apple 替身、真实插件注册/会话/运行/cron 挂接、进程退出与 lookup 测试 |
| `test_http_contract.py`、`test_text_contract.py` | A5 完整错误映射、interpret 全程超时、紧凑 UTF-8、items 响应字节边界及无损长标题/originals |
| `test_schema_errors.py` | N3 字段字节超限 413、组合 schema 内层错误与合法替代分支、字符/结构错误 400 |
| `test_collection.py`、`test_apple_config.py` | D12 范围/窗口/500 项、失败不冒充空结果、真实 cron 路径与零工具、启动映射校验；原生纯逻辑自检覆盖双向名称和 originals 比较 |
| [C → D 衔接核对](docs/gateway-handoff.md) | 网关源码核对、逐接口路径/字段/认证、可信身份链与 capture 核对分工 |
| [GTD Skill 候选](docs/gtd-skill-candidate.md)、[现行 gtd 差异候选](docs/gtd-skill-update-candidate.md)、[四任务完整提示词候选](docs/cron-prompts-candidate.md) | 已对照提供的原任务/Skill 快照，只供审阅，未安装、未更改真实任务 |

## 换清单：AppleScript move（2026-10-15 开发变更，未部署）

真实 iCloud 账户用 EventKit 更换 `reminder.calendar` 会在保存/commit 时失败（`com.apple.reminderkit/-3002`）。本次直接用「提醒事项」App 的 AppleScript `move`，不先尝试 EventKit，也不复制后删除。证据、全部预算、合同影响和验收清单见 [本次记录](../../custom/records/2026-10-15-ollo-bridge-applescript-move.md)。线上事实来自用户提供的设计会话，本次未接触真实提醒事项。

helper 采用可替换的 `ReminderMoveRunner`，生产实现是进程内 `NSAppleScript`。先检查整组 originals，再用 `commit:false` 预检其余字段并 reset；按精确 `reminder id` / `list id` 移动，每次通过 EventKit 重新读取，要求目标清单、原始标题、完整 creationDate、未完成状态恰好匹配一条，且旧编号不存在。编号只接受 UUID，作为 Apple event descriptor 参数传入，绝不拼接脚本或按名称移动。缺创建时间、external/local 编号不同、目标存在同特征事项时拒绝移动。

成功找回后在本组内替换旧引用，再按原顺序暂存其他字段，只 commit 一次；merge 目标随后换清单也使用找回的新对象。多个移动按首次涉及的顺序串行，同一事项多次指定清单取最终目标；每次发送前要求剩余软预算至少 27 秒。已有移动后发生失败或预算不足，整组为 unknown，不能逐项假报 done，也不会尝试搬回原清单。未移动的独立组仍可继续。

自动化权限与 EventKit 读取权限是两项授权。**部署后须由用户在 Mac mini 屏幕上进入「系统设置 → 隐私与安全性 → 自动化」，允许负责进程控制「提醒事项」。** 用户提供的启动链以 osascript 为负责进程；实际条目以系统显示为准，不能保证重新签名/更换启动链后沿用旧授权。`Info.plist` 已加入 `NSAppleEventsUsageDescription`。首次调用可能后台启动提醒事项 App 并弹窗；应在部署验收窗口处理，拒绝返回固定权限原因，不输出系统原文。

AppleScript 的命令回复超时为 12 秒，移动前读取最多 5 秒，移动后核对最多 5 秒；apply 软预算 48 秒、看门狗 55 秒、Python helper 58 秒、每组操作 62 秒。普通读取、capture、纯日期/delete/to_event 预算不变。NSAppleScript 随 helper 被终止，避免遗留 osascript 子进程；已发出的 Apple event **不因超时而取消**。因此 -1712/-609 后先核对：唯一找回则继续；旧事项暂时仍在也保留 unknown，不能承诺以后不会移动。此处采用比初始任务要求更保守的分类，依据 [Apple 的超时说明](https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_control_statements.html)。

**移动会改变事项编号。** 本组内部可继续 update/merge；现有 `GTDResultEntry` 仅有 `index/status/reason`，没有新编号或旧新映射字段。成功回执只返回 `status: done`，不附 `reason`；编号变化及处理限制只记录在本文和维护记录，不作为界面提示，不改合同，也不把编号塞进文案当隐式协议。旧报告的 items、重点选择和后续卡片仍引用旧编号；刷新旧报告的 items 只能看到旧项不存在，必须重新生成报告取得新编号。重复提交完全相同的业务组仍由网关重放原回执，不重新移动。

设计会话已在沙箱外复核：`hermes-plugins/` 26 个文件、553 项全过，helper `--self-test` 返回 `{"ok":true}`（用户提供的结果，发生于本轮文案调整前）。本轮将失败原因改为指定的简短事实文案，成功回执不附 `reason`；不在确认结果中解释编号或提供操作指南，权限授予步骤仍保留在文档中。本轮调整后的 Python 回归需沙箱外补跑；真实权限与链路仍待部署后验收。独立开发产物位于 `$HOME/.cache/hermes-agent-dev/ollo-move-20261015/`；本轮文案复核构建另放在 `$HOME/.cache/hermes-agent-dev/ollo-move-20261015-copy-review/`。只可运行 `--self-test` 做离线验证，普通执行会访问 EventKit。

## 复用与宿主增量

复用 `PluginContext` 平台/路由/工具/hook/middleware、`ctx.llm.acomplete_structured`、辅助任务路由与凭据、`SessionDB.resolve_resume_session_id`、宿主 cron jobs/executions/delivery queue。上下文追加到本轮用户消息，保持既有 system prompt、工具集合及历史缓存前缀。

源码核验发现：仓库只有 Apple Reminders 的 `remindctl` Skill，没有批量提醒事项+日历读写与依赖组单 commit 的原生核心工具。本插件从旧隔离日程审查副本复用并调整了 EventKit helper，**不是从运行目录复制，也不表示 helper 已部署**。其 `items` 一次读精确 ID 集合；`capture` 原文写固定「收件箱」；`apply` 先检查整组生成时 originals；普通变更按顺序 `commit:false` 暂存再只调用一次 commit，换清单的外部移动流程见上节；`collect` 提供只读报告采集与同次 originals。未完成/已变更/缺快照整组 skipped；写入超时、管道损坏、commit 失败均 unknown，不能承诺 Apple 跨设备原子回滚。

D2 候选共保留一项通用核心补丁，A4/A5 均未扩大：`cron/delivery_retry.py` + `delivery_queue.py`/`scheduler_delivery.py`。v0.21.6 队列原本失败即终结。仅 live router 认证的 **单目标、无附件、非 relay、`idempotent_delivery=True`** 获得重试资格；任务字段不能自行开启。进程内调度先入原队列并立即尝试一次，失败由既有 drain 重试；相同执行 ID 和内容最多 5 次，间隔 1/5/30/120 秒。认证行的 pending/in-flight 不报成功，不走 standalone。其他平台保留原 at-most-once 语义。完整理由、上游状态与退出条件见 [补丁清单](../../custom/patches.md#未提交幂等-live-adapter-的有界投递重试)。

以下复用已有能力，无新增核心补丁：

- `execution.py` 按现有一行补丁透传的 execution_id 精确查 `cron.executions.get_execution()`，取 started_at/scheduled_instant。wake 已在调度锁内把网关 operation_id、业务日期和 manual_run_at 记入 job；进程内与外部 worker 均在首次发送前入原队列，投递只读同 execution/job 的不可变快照，精确匹配 manual_run_at。即使次日才开始或后来 wake 修改 jobs.json，仍保留原 trigger_id/业务日期；固定调度不继承旧 trigger。缺失执行账本或队列快照均拒绝投递，不以开始/到达时间补造 wake 身份。只把 UUID 规范化用于 wire，宿主账本查询仍用原始 ID。
- 快照只在 pending/delivering 的投递生命周期保留；bridge 在 adapter 发送时读取，此时队列仍是 delivering。成功返回后，宿主 `_finish` 清空终态的 job_json/content，之后只用于结果查询/幂等去重，不能当历史任务快照读取。相同 execution 再次进入宿主投递会直接命中终态，不再调用 adapter；不为 bridge 延长敏感载荷的保存期限。
- 失败类型取原队列独立的 `for_failure` 列，输出 `delivery_kind: failure/report`；不读模型正文、adapter metadata 或 job 中同名字段。Hermes 正常失败/崩溃通知经 `_deliver_result(for_failure=True)` 进入该列；现有 metadata 没有这个标志，但无需新增宿主传参。A4 在网关侧把 failure 保存为纯通知，不解析 GTD、不占用 agenda 日唯一键；实际服务联调仍待完成。上述进程内路径要求单目标 Ollo、无附件、非 relay；缺队列的直接 adapter 调用不会假报成功。
- v0.22.1 canonical 路径：`authority_identity.py` 要求宿主 `api_execution` 指向同一 adapter，当前 home 的 authority 正在执行，并核对执行 epoch/generation、该 generation 接管的活 agent 和精确 session/task/turn。再从同一 admission 读取 `request_id`：宿主 POST run 将自己生成的 run_id 存在这里，同时核对 admission 的 API owner scope。approval ContextVar 在此路径只是会话路由，**不能用作 run_id**。结束后 executing 标记清除，旧 agent/旧上下文即使仍存在也没有身份。
- authority 为 None 的 legacy 路径保留 approval ContextVar → `_active_run_agents[run_id]` → 精确 session/task/turn 的校验，绕开环境变量 fallback。两条路径都要求 Ollo root/压缩续接、相同 Profile、API 平台且非委派；确认发送 **实际 run_id 作为 X-Ollo-Task-ID/runtime.task_id**。正常网关先初始化 authority 再开放 API（`gateway/run_bootstrap.py:717`、`:773`）；无 runner 的独立 legacy adapter 仍可读取自身 SessionDB。人为构造或故障下有 runner、无当前 authority 时，谱系无法证明，保持拒绝 Ollo 身份。以上私有宿主接口需随升级回归，无新增核心补丁。
- `pre_tool_call` 是全进程 hook。缺少前置上下文且谱系属于 Ollo 或无法判定时，只有宿主证明是本 adapter 的 API 执行才拦截通用工具：canonical 要求 `api_execution.adapter is adapter`，legacy 要求 approval ContextVar 指向本 adapter 的活 agent。存储缺失或 SQLite 错误不会阻断 cron、其他平台或其他 adapter 的通用工具；Ollo 专用工具仍必须有可信回合上下文。
- 请求 UUID 只来自通过宿主认证的单个 `Idempotency-Key`。canonical FIFO 可能在 HTTP admission 返回前开始执行，所以中间件为新请求短暂保留 owner/UUID/宿主请求指纹，仅在宿主幂等 store 已将它绑定到当前 run 时提供 UUID；handler 返回后沿用 active task 的内存登记，结束撤销。既有 key 的重放不新建许可，请求退出清除 pending，重启不从持久化 key 恢复确认许可。正文中的标记只收紧工具范围，不能签发身份。
- canonical GET run 状态的 session_id 是逻辑 FIFO 根会话，中间件在原认证/owner 检查后解析压缩续接，导出与工具一致的实际 session_id 和 `task_id=run_id`；否则有压缩历史的确认仍会被 Ollo 网关拒绝。读取或解析数据库失败（包括谱系检查成功后数据库变为 None、SQLite/OSError）时保留宿主原响应，不部分改写，也不因插件投影返回 500。

Telegram 话题路由及原 relay 测试已恢复，未搬移。相关记录见 [fork 修订记录](../../custom/records/2026-10-11-ollo-bridge.md)。

## 配置（全部由用户在部署窗口写入）

路径相对于当前 Profile 的 `get_hermes_home()`；不硬编码默认 home，不回退旧 inbox/location 配置：

- `ollo/bridge.json`：非密钥配置，加载时读取。
- `ollo/bridge.key`：至少 32、至多 512 个可见 ASCII 字符，文件权限 0600，独立于网关 owner key、Hermes API key、模型密钥。由用户写入，助手不接收、不生成真实密钥。inbound/outbound 使用同一 bridge Bearer。代码拒绝与当前 Hermes API key 相同的配置；与网关 owner 域的分离还须由网关配置确保。

`bridge.json` 示例：

```json
{
  "session_id": "EXISTING_OLLO_API_ROOT",
  "name": "Ollo",
  "gateway_url": "http://127.0.0.1:8765",
  "helper_path": "/ABSOLUTE/OPERATOR/CHOSEN/ollo-eventkit",
  "jobs": {
    "EXISTING_WEEKDAY_AGENDA_JOB": {"title": "今日安排", "kind": "agenda"},
    "EXISTING_WEEKEND_AGENDA_JOB": {"title": "今日安排", "kind": "agenda"},
    "EXISTING_INBOX_JOB": {"title": "收件箱整理", "kind": "inbox"},
    "EXISTING_REVIEW_JOB": {"title": "每周回顾", "kind": "review"}
  },
  "agenda_jobs": {
    "weekday": "EXISTING_WEEKDAY_AGENDA_JOB",
    "weekend": "EXISTING_WEEKEND_AGENDA_JOB"
  },
  "apple": {
    "lists": {"收件箱": "收件箱", "工作": "工作", "生活": "生活", "家庭": "家庭", "等待": "等待", "长期": "长期"},
    "calendars": {"工作": "工作", "生活": "生活", "家庭": "家庭"}
  },
  "collection": {
    "agenda": {"lists": ["收件箱", "工作", "生活", "家庭", "等待", "长期"], "calendars": ["工作", "生活", "家庭"]},
    "inbox": {"lists": ["收件箱"], "calendars": []},
    "review": {"lists": ["收件箱", "工作", "生活", "家庭", "等待", "长期"], "calendars": ["工作", "生活", "家庭"]}
  }
}
```

只接受固定 `http://127.0.0.1:<port>` 或 `http://[::1]:<port>`，无用户名、路径前缀、query 或 fragment；不解析任意域名，HTTP 禁用重定向和环境代理。loopback、Host、来源 IP、转发头均不能免除 Bearer。配置不含 binding_id：网关的 bridge key 固定绑定其配置的一个 binding，与此处 session root 和四任务白名单逐项配对；请求不能覆盖它。

### Hermes config.yaml 的精确部署差异（D13）

以下现状由用户/设计会话提供，未读取运行配置：plugins.enabled 为 bot-handoff、homeassistant、ollo-conversation、ollo-inbox、ollo-location、orca-status；entries 含 `ollo-location: {allow_tool_override: false}`；photon/hermes_plus/ollo 平台均已 enabled；没有 ollo/api_server 的 platform_toolsets 白名单，也没有 timezone 项，本机时区为 Asia/Shanghai。

| 配置路径 | 此次差异 |
| --- | --- |
| `plugins.enabled` | 删除 ollo-conversation、ollo-inbox、ollo-location，添加 ollo-bridge；保留 bot-handoff、homeassistant、orca-status。 |
| `plugins.entries.ollo-location` | 删除已停用插件的这条旧配置，作为清理；留着不会启用旧插件，但不再需要。其他 entries 保留。 |
| `plugins.entries.ollo-bridge` | **不必新增或由旧项改名。** bridge 注册工具不使用 override，缺省也不授予 tools.override；无需 allow_tool_override。若运维坚持显式记录 false，可写同值，但不是接入所需差异，不能改成 true。 |
| `platforms.ollo.enabled`、`platforms.hermes_plus.enabled`、`platforms.photon.enabled` | 本次 bridge 接入无配置增量，保留当前值；旧消息渠道的整体停用不在这份接入差异中。四个 GTD 任务的 deliver 改为 ollo:main 是任务配置，不在 config.yaml。 |
| `timezone` | **必须**写 `timezone: Asia/Shanghai`。`wake.py` 要求 `cron.get_timezone()` 的 key 为 Asia/Shanghai，未配置时它返回 None（跟随本机），wake 一律 `503/not_configured`。2026-10-12 首次真机「我醒了」因此失败，加上后重启修复；本机为 +08:00 时加此项不改变任何任务的 next_run_at。 |
| `platform_toolsets.ollo`、`platform_toolsets.api_server` | **不新增、不改写。** 保留 Ollo 对话原有工具能力，包括 Apple Skill 所需工具。 |

修改后的 enabled 列表为（只替换此列表，不覆盖其他 plugins 配置）：

```yaml
plugins:
  enabled:
    - bot-handoff
    - homeassistant
    - ollo-bridge
    - orca-status
```

此前“原工具白名单改成 ollo_bridge”是文档错误，并非已确认存在的另一处配置。`ollo_bridge` 是 `integration.py::register` 注册三个工具时使用的 **toolset 名**，不是 plugins.entries 权限项，也不要求成为唯一可用工具组。Ollo App 对话实际经宿主 `api_server`：`gateway/platforms/api_server.py::_create_agent` 调用 `hermes_cli/tools_config.py::_get_platform_tools(config, "api_server")`；未保存平台白名单时走原平台默认工具，并按既有规则纳入新插件工具组。不要创建 `platform_toolsets.api_server: [ollo_bridge]` 或 `platform_toolsets.ollo: [ollo_bridge]` 来替换整个工具选择。四个 cron 的 `enabled_toolsets: []` 只限制报告生成任务，不限制普通对话。

源码依据：`hermes_cli/plugins.py::PluginContext.register_tool` 与 `plugin_capabilities.py::plugin_capability_granted` 说明 allow_tool_override 是覆盖同名内置工具的授权，省略默认不授予；本插件未请求该能力。`hermes_time.py` 的解析顺序为生效的 HERMES_TIMEZONE、配置 timezone、服务器本地时区，`cron/jobs.py` 复用该时区计算 cron；本轮没有读取进程环境或管理配置。bridge 自身的业务日期/采集窗口固定用 Asia/Shanghai。

本插件需要 live adapter，`plugins.isolation: host` 不支持此宿主表面；不得绕过管理员锁定的隔离策略。依赖 `jsonschema>=4.23,<5` 由插件 manifest 声明，用 Hermes PM 插件安装/启用流程准备，不用裸 pip/uv。

可选辅助模型配置 `auxiliary.ollo_interpret` 复用宿主 provider/model/timeout；没有独立模型密钥。该辅助调用只接结构化消息，无 Agent/tool loop，不写对话、memory 或历史。语气完全不在 bridge 中处理。

## 接口与工具边界

| 入口 | 认证与行为 |
| --- | --- |
| `POST /v1/ollo-bridge/schedule/{wake,capture,items,interpret}` | 独立 bridge Bearer，只匹配四个名字；schema 闭合，无任意命令、工具名、cron ID；wake/capture 16 KiB、items 640 KiB、interpret 4 MiB，覆盖 A4 最大引用集合 |
| `POST /v1/ollo-bridge/schedule/capture/lookup` | 独立 bridge Bearer；4 KiB，`{operation_id,request_id,attempt}`；同 request_id 的 helper 活动时 HTTP 200 unavailable/in_progress，否则按 URL 只读查询 |
| `GET /v1/ollo/conversation`、`/v1/hermes-plus/conversation` | 保留宿主 API Bearer 与 `{session_id,name}`，bridge key 不能调用；方便旧 conversation 停用后发现仍可用 |
| 平台 `ollo:main`、别名 `hermes_plus:main` | live send 原样保留 GTD 围栏，标题取本地 job 白名单；execution UUID 与开始时间来自宿主，delivery_kind 来自当次队列行。agenda 日期取原调度时刻/开始时间的上海日期，wake 取当次队列快照的触发日期；缺可信身份拒收。网关校验截点、同执行内容冲突和正式报告每日唯一键；suppressed 是成功，HTTP 失败绝不报成功 |
| `get_gtd_reports` | 可信当前 API 回合，读同一网关报告；最近列表/向前分页或指定 UUID，不重跑 GTD |
| `get_user_location` | 模型只给 max_age/timeout；runtime_call_id 由宿主 ID 摘要生成，POST 一次、短轮询，总截止 ≤15 秒且不续期；本批只接受 iPhone 结果，坐标时间最多领先本地时钟 10 秒（含边界） |
| `execute_gtd_confirmation` | 模型只给 confirmation_id；确认必须绑定当前真实 API run。GET 授权后逐组 claim，取网关原始 changes/originals，helper 执行后 finish，只有持久收到 accepted 才返回 done |

本插件的三个工具需可信 `pre_llm_call` 回合 + 相同 Profile + api_server root/压缩续接；分支、reset、委派、其他渠道不行。报告卡片确认标记回合通过宿主 `pre_tool_call` 和 `tool_execution` middleware 限制其他工具，包括 terminal/execute_code/委派；即使 pre hook 缺失，活跃 Ollo API 回合也保守拒绝工具调用。工具参数中的伪造 session/task/run/changes 均拒绝。

写入授权按来源区分：定时报告提出的建议必须经 Ollo 卡片确认，由 `execute_gtd_confirmation` 执行，普通文字“确认”不授权这类修改；用户在普通 Ollo 对话里直接提出修改请求，仍先列出确切变更、取得对话明确确认，再用 `apple-reminders` / `apple-calendar` Skill 执行。后者不要求卡片确认 ID，也不套用报告生成模型的空工具集。既有运行时仅对卡片确认标记回合施加该工具限制（异常缺少可信回合状态时仍按原规则拒绝执行）；D13 不改代码。

wake 在与宿主相同的线程锁和 `.jobs.lock` 文件锁中检查 runnable/paused，锁故障/超时直接失败；不调用会自动恢复暂停的裸 trigger_job。收据保存在既有 jobs.json 的 external_trigger，队列意图和收据一次保存；不建第二个调度库。已到固定时刻/已在运行时不覆盖其 next_run_at。同一天已触发不再重排；提前执行失败后，原 cron 表达式仍保留固定兜底。wake 入队不等于报告生成；只有网关已保存报告才返回 already_generated。

capture 先 claim，使用许可中的完整原文写固定「收件箱」，不调用模型或截断。capture 原文和 items 响应 title 均按合同限制为 4096 UTF-8 字节，超限拒绝，不按字符数截短；helper 输入使用 UTF-8 JSON，100 个最长多字节 ID 也可装入其请求预算。读取与 interpret 先查网关已保存报告的引用。items 失败不假空，结果集合精确；不会修改 originals。确认共享 item/into 不允许被拆成独立组。未知写入/丢失 finish 回执不会重写，后续只观察网关状态；unknown 无本地 TTL，也不做标题搜索“恢复”。

A5 统一所有插件生成的 JSON 为紧凑 UTF-8（Python `ensure_ascii=False`），只保留 JSON 必要转义；字节预算按实际编码正文计算。items 响应上限 **2097152 字节**，包含必要控制字符转义；helper 只输出公开字段，超限返回 `413/payload_too_large/false`，由网关/App 缩批重取，不返回截短的 200。GTD 所有标题类字段和 originals 同步 4096 UTF-8 字节上限；只读采集、报告及确认前原值核对均保留完整标题。

EventKit 的 prepare/commit 失败另附内容无关的 `diagnostic`：phase、NSError domain/code、op、有效请求 fields；直接 underlyingError 仅保留 domain/code。`apple.py` 和 `apple_diagnostic.py` 过滤字段，`schedule.py` 在失败分支记录一条 `WARNING EventKit failure <JSON>`，JSON 仅含 operation_id 与 diagnostic。诊断不进入网关 finish 或用户结果，原有 not_executed/unknown 分类、回执重放和禁止盲重试语义不变；不记录描述、userInfo 或 stderr。

操作员可直接向 helper 发送 `{"method":"inspect","ids":["ID_1","ID_2"]}`，按输入顺序取得 EKReminder 的结构信息，不回显 ID 或内容。仅支持该精确请求形状；无 HTTP/模型工具入口，不申请权限，不写入或重试。字段含义、沙箱外 `swiftc -O`/Info.plist/签名/自检、部署与回滚和两条 ID 的私密输入命令见 [诊断维护记录](../../custom/records/2026-10-15-ollo-eventkit-diagnostics.md)。本补丁尚未部署，完整 bridge 测试在沙箱内 15 个文件均未能启动，须沙箱外补跑。

interpret 的 18 秒总预算覆盖报告读取、模型等待和输出校验；任一超时或预算耗尽归一为 `504/interpret_timeout/true`。`protocol.py` 与 HTTP 客户端共用合同完整错误表，按 code 固定合法状态和 retryable；401/unauthorized、410/expired、413/payload_too_large、503/not_configured 等永久失败不会被改成可重试。未知或无法解析的上游错误统一隐藏为 `503/dependency_unavailable/true`，旧错误的未知重试语义保留为省略 retryable。

字段级 `x-max-utf8-bytes` 超限同样返回 `413/payload_too_large/false`，遍历所有校验错误及 oneOf/anyOf/allOf 内层错误，避免先出现的 maxLength 或结构错误吞掉 413。其余 schema 错误仍为 400；items ID 等只限字符数的字段不增加字节约束。interpret 模型输出不论字节超限或结构非法，仍统一为 `503/invalid_model_output/true`。

capture 的 URL 固定为 `ollo://capture/<request_id>`，不写进标题或备注。lookup 的许可过期 + 30 秒和当前 attempt/unknown 校验由网关负责。bridge 不保存执行记录：内存仅登记活动 request_id（含迟到 spawn 的清理），结束即移除；重启后空登记直接查询。仅完整提醒权限下遍历全部清单（含已完成事项）且无匹配才 absent；权限不足、超时、不完整或多重匹配均 unavailable。

原生 helper 在读取 stdin 前启动独立 `alarm(7)`，短于网关 8 秒执行许可，恢复并解屏蔽 SIGALRM 的默认终止行为；即使父进程退出或 EventKit 阻塞也自行终止。bridge 仍按自身预算 kill 并回收（capture 最多 6 秒、lookup 最多 5 秒），不能把超时当作未写入。capture/lookup/collect 等普通请求不会重新计时；只有已解析的换清单 apply 重设为 55 秒。用户手动 `--authorize` 的授权流程不设看门狗，该分支不写事项。

## 从测试任务切到正式任务（2026-10-14 11:36–11:39 已执行）

按 [正式切换方案](../../custom/records/2026-10-14-ollo-gtd-production-switch.md) 的命令和授权节点执行，不重复下面的初次安装。四个真实任务保持 ID、名称、时刻、model，单投 `ollo:main`，加载 `gtd` + `hermes-plus-gtd`，使用对应采集入口、空工具集、`attach_to_session=false`。删除测试四任务和 `gtd-ollo-test`，正式 gtd 从已验收测试 Skill 生成；删除 bridge 的 `apple` 映射，按缺省同名读取真实六清单、三日历。

工具默认 `plan`，只有 `apply --backup-dir <新目录> --quiesced` 和 `rollback --backup-dir <原目录> --quiesced` 写文件。通过方案里的 `hermes_script` 函数复用已安装 Hermes 的 `runtime_command(code=...)` 执行，显式指定 HERMES_HOME；保留完整插件源码目录供候选和脚本对比。工具不调用 helper、不读 key，不替用户更新 Ollo 网关白名单，不停启服务。首尾空白以外的测试 prompt/候选差异、非默认采集范围或运行字段漂移都会阻止切换。

`bridge.json` 在常驻插件注册时读取，采集脚本则每次读取，所以必须在两个网关与写入/投递都停止并排空的窗口操作。Ollo 白名单先加入真实 ID，再移除测试 ID；协调停机方案让 Hermes 和 Ollo 各停启一次。今日安排仍按 binding + 业务日去重，当天已有测试报告时，换 ID 不会取得第二份名额。首次真实运行、周日回顾和任何真实 Apple 写入都按方案单独验收。

三个候选文档标为“将于正式切换时安装”。这里的“安装”指正式任务采用候选 prompt、测试 gtd 转正式；已经安装且验收的 `hermes-plus-gtd` 保留，不用仓库候选覆盖运行版。新工具与全部 bridge 测试在本次沙箱内均未能启动（PermissionError，0 collected），执行前须在沙箱外补跑，不能视为已通过。

## 初次部署顺序（历史候选）

1. 在沙箱外跑下面测试及 B+C/D 联测，核对源码、模型替身结果与跨仓库 run 映射；生产 `schedule` 保持关闭。D12 已对照提供的四任务原提示和 Skill 快照，审阅完整候选及下文的脚本失败/跨日边界。
2. 在授权窗口暂停新调度、旧投递/推送，按方案一致备份旧插件、配置和 SQLite；不读旧业务表，不导入/重放旧数据。明确切换截点，隔离截点之前或缺可信时间的执行与旧队列。
3. 用户在网关与 Hermes 终端写入独立 bridge key、匹配 binding/根会话/四任务白名单、回环 URL/端口与网关切换截点。按选定绝对路径编译/安装 `native/EventKitBridge.swift`，链接配套 `native/Info.plist` 的权限用途说明（`swiftc … -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker native/Info.plist -o …`）；由用户显式完成 Apple 提醒事项与日历权限。`--authorize` 才会请求 EventKit 权限；确认换清单发送 Apple Events 时可能另弹自动化授权，见下节。
4. 部署 fork 的宿主增量和**完整**插件目录（运行必需 `.py`、plugin.yaml、contracts/schema.json）；按上文精确差异替换 plugins.enabled，清理旧 location entry，保留旧目录。不新增 Ollo/API 工具白名单，不要求新增 bridge entry，但必须写 `timezone: Asia/Shanghai`（见上表），现有平台 enabled 配置无需为 bridge 改写；同一平台/工具/发现路由不能重复注册。按已有授权维护流程排空并重启 Hermes；本次未授权自动执行此步。
5. 按下一节安装四个脚本文件，更新现行 gtd 的差异和 v1 Skill、四段完整提示词；保留 job ID、08:00/09:30/21:00 时刻、模型与 repeat。全部单投 Ollo、无附件、attach_to_session=false、enabled_toolsets=[]。核对 failure_deliver 为空继承或同样单投 Ollo；这只配置真正宿主失败的路由，不会把 Script Error 自动升级为失败投递。
6. 配置 `apple` 把六清单、三日历分别对应到「Ollo 测试·…」，重载配置后用专用假事项验收 wake 兜底、capture、items/interpret、确认、Apple/iCloud 回执；另验收 App/SSE、通知、位置。APNs 与自动位置在网关配置，手机重新开启授权。完成后撤掉测试对应表并重载；测试报告/确认不能继续作为生产写入授权。解决下面失败/跨日边界并按单独发布授权打开能力、恢复自然调度。

回滚须先停新写入并保存新网关回执与宿主队列；不能恢复旧数据库抹去 unknown/已执行结果。可以停用 bridge、关闭相关能力、保留新状态向前修复；不要自动复活旧定位授权或重放旧报告。Apple 写入不能靠还原 SQLite 撤销。

## 只读采集脚本与名称对应表

`apple` 与 `collection` 都可省略，默认值就是上面示例。`apple.lists` 只接受六个逻辑清单名，`apple.calendars` 只接受三个逻辑日历名；允许局部覆盖，其余保持同名。实际名称非空、最多 128 个 Unicode 码点。每张表必须一一对应，连同缺省值一起检查（例如只把工作映射成生活也会冲突），包括 Unicode 等价名称；未知键/重复值在插件启动时拒绝，adapter 不会连接。清单和日历是不同命名空间，可以各有一个“工作”。配置校验不访问 Apple；实际清单不存在、同名多份或权限不足在采集/操作时明确失败。

联合验收可将实际值设为 `Ollo 测试·收件箱`、`Ollo 测试·工作` 等，逻辑键和 collection 不变。capture 固定写 `lists[收件箱]`；apply 的 list/calendar 目标经过同一表；collect/items/originals 将已映射实际名还原为逻辑名，表外实际名原样返回。确认前重新读取也做同样转换后比较 originals。lookup 为核对历史 URL 仍扫描全部清单，不被测试表限制。配置变化须在停止相关工作后重载插件；脚本每次读取配置，不能在 helper/报告/确认进行中切换名称对应表。

`collection.<kind>.lists/calendars` 是逻辑名数组，可局部配置，未知名/重复名/空 lists 拒绝。它只选择范围，不改限额或提示词业务规则；正式四任务保持默认六清单/三日历，测试用 apple 映射隔离。时间按 Asia/Shanghai：

| 入口 | 未完成提醒 | 日历窗口 |
| --- | --- | --- |
| `ollo_collect_agenda.py` | 六清单全部，包括任意久的超期、未来到期、无日期项；模型再选今日及重点 | 当天 00:00 至次日 00:00，三日历 |
| `ollo_collect_inbox.py` | 仅收件箱 | 不读日历，也不要求日历权限 |
| `ollo_collect_review.py` | 六清单全部 | 采集开始时刻起七天，三日历 |

helper 一次读取选中提醒集合、一次读取选中日历集合；先过滤已完成提醒，再计算未完成提醒+日历事件总数，**500 项为硬上限**。501 项报 `collection_limit_exceeded`，不截断、不返回空成功。权限不足、缺失/重复名称、读取超时、重复事项 ID 或不完整结果同样失败。items 和同次 originals 保留完整原值，stdout 恰好一份紧凑 UTF-8 `{items,originals,collected_at}`；零项也输出非空 JSON。采集输出另有 16 MiB 总预算，超限失败，不截数据。报告 v1 的 100 项数组/128000 字节正文限额独立存在，采集成功不等于一定能生成合法报告。

安装时复制**普通文件**，不要软链接到仓库：从插件 `scripts/` 复制 `ollo_collect.py`、`ollo_collect_agenda.py`、`ollo_collect_inbox.py`、`ollo_collect_review.py` 到当前 Profile 的 `<HERMES_HOME>/scripts/` 根目录，用户拥有且其他用户不可写。同时安装完整插件到该 home 的 `plugins/ollo-bridge/`，包含 `collector.py` 和 `apple_config.py`；配置绝对 `helper_path` 指向已编译的 helper。三个入口从自身安装路径确定所属 home，仅加载 stdlib collector，不执行插件注册、不读 key。任务 script 仅填表中的文件名，无命令行参数；Python 由宿主 PM/任务 interpreter 选择，脚本不需要 executable 位。

已核验的宿主语义：`cron/scheduler_script.py` 的 `_resolve_script_path/_script_argv/_run_job_script`，`cron/lifecycle_guard.py` 的 script 解析，`scheduler.py` 的 pre-run gate，以及 `scheduler_prompt.py::_build_job_prompt`。相对路径从 owning home/scripts 解析，绝对路径/软链接最终也必须位于此目录；script 字段不是 shell 命令。子进程使用宿主 `build_subprocess_env(strip_launch_profile=True)` 的过滤环境及所属 Profile HERMES_HOME，没有额外 job/run/原调度时间参数或环境变量。cwd 为任务 workdir 或脚本目录；stdout/stderr 经宿主脱敏。脚本总超时由既有 HERMES_CRON_SCRIPT_TIMEOUT/cron.script_timeout_seconds 决定，默认 3600 秒；本采集器调用 helper 自行限 6 秒并 kill/reap，helper 另有独立 7 秒看门狗，采集不重试。宿主取消/超时还会终止脚本进程树。

预执行 stdout 缓存后只注入一次 Script Output，prompt 构建不会再执行。成功但空 stdout 会跳过模型，故零事项也不能输出空白；显式 `enabled_toolsets: []` 阻止宿主合并 MCP 工具。**普通 agent cron 的脚本非零退出/超时只会注入 Script Error，仍调用模型，不会自动变成 for_failure。** 候选提示词明确报未生成且不输出假空卡片，但成功完成的模型回复仍按 report 投递，可能消耗 agenda 日账；此保证必须联调并单独解决，不能把提示词当失败状态挂接。`no_agent=true` 虽会直接传播脚本失败，却不满足本次由无工具模型生成报告的需求。

脚本无法读取可信原 wake 日期，窗口取实际采集时间；外层 business_date/trigger_id 仍来自当次执行快照。跨午夜延迟启动时两者可能不同，需要确定过期执行处置后上线。本轮未新增或扩大宿主补丁。

## 测试与尚未验证的挂接

设计会话首轮在沙箱外运行：`tests/cron/` 全部通过，三个旧插件通过；bridge 的五个测试文件均因 pytest 提前裸导入 `__init__.py` 而未进入测试。在父 `conftest.py` 中仅对 bridge 使用 `pytest.Dir` 后，沙箱外复测确认加载修复，bridge 为 39/42 通过，其余插件全过。测试仍经隔离 `runtime` fixture 的真实 `PluginManager` 按包加载插件，宿主加载器不变。

当时三项修复：`test_runtime` 导入 `run_agent` 前绑定 fixture manager，避免第二次发现造成工具与 hook 实例分离；离线 schema 补齐公共入口 `P_GTDResult` 的引用闭包。此前沙箱外复测为 190 项通过、1 项失败：新加入的确认 UUID 透传误以为宿主 executor 复制请求 ContextVar，实际 `_submit_api_worker` 使用 `run_in_executor`，只重绑宿主身份。已改成认证后按新接受的 run_id 暂存 UUID，任务结束删除，不对重放重新授权。原端到端断言保留，补同会话多 run、executor 跨线程、owner/adapter 隔离及完成撤权测试；最新第六轮结果中该项已通过。

A4 新增真实临时 cron 的 wake → due scan → fire claim → 进程内投递 → HTTP 失败 → 原队列重试测试，同时覆盖次日开始、后来 job/调用字典变化、正式/失败类型和固定兜底。另补位置新鲜/缓存结果 10 秒边界、最大引用集合 HTTP 正文预算、标题 UTF-8 上限与完整 capture、helper 7 秒自退测试。没有新增或扩大宿主补丁。

第六轮沙箱外结果：插件 215 过、2 失败，cron 1485 全过。两个失败均是测试在成功 drain 后仍要求完整 job_json，而宿主此时已按既有策略清空终态载荷。现在 HTTP 替身在返回 201 前只读采集真实 delivering 行，测试保留完整快照逐字相等、正文及 for_failure 不变、两次 HTTP 载荷完全相等的断言；另检查成功后清理与重复投递不再发 HTTP。那轮只修测试及生命周期说明，生产 bridge、队列和核心补丁均未改；修正后的通过结果见下方 A5 复测。

A5 根据第二份对账报告补了完整错误表、报告读取/模型/预算各阶段超时、100 条最长 emoji 标题、2 MiB 前一字节/精确边界/超一字节与控制字符转义、513 字符和中英文/emoji 4096 字节标题、长 originals 到确认 helper 的测试。原生纯逻辑自检覆盖采集原值与比较的无损性。HTTP 替身使用客户端实际指定的 JSON serializer，避免替身掩盖 ASCII 转义错误。本轮仍无宿主增量。

设计会话随后在沙箱外完成 A5 正式测试：`hermes-plugins` 286 passed / 0 failed，`tests/cron` 1485 passed / 11 skipped（上游既有跳过），覆盖此前队列断言时序修复。第三轮 N3 新增 HTTP 字段字节超限/字符超限、组合 schema 和模型输出分类测试，尚待下一轮复测。本会话仅作静态检查，不在沙箱内运行测试或 helper，不访问真实 home/密钥或重启服务。D1 已由设计会话核验与线上逐字一致并确认通过；测试通过仍不能代替 Apple/iCloud 和真实服务联调。

设计会话在沙箱外：

```sh
export HERMES_RUNTIME_DIR="$HOME/.cache/hermes-agent-dev/tools"
export HERMES_HOME="$HOME/.cache/hermes-agent-dev/home"
source ./activate
scripts/run_tests.sh hermes-plugins/ --file-retries 0
scripts/run_tests.sh tests/cron/test_delivery_retry.py tests/cron/test_delivery_queue.py tests/cron/test_cron_relay_dm_origin_user_id.py --file-retries 0
python scripts/check --base v0.21.6
```

先核对测试文件路径是否仍在当前基线；依赖由隔离 PM 环境提供。`test_apple_contract.py` 的 macOS 测试共享一次编译：纯逻辑执行 `--self-test`；看门狗测试保持 stdin 打开且不供给数据，断言约 7 秒后由 SIGALRM 自行终止。两者均不构造 EventStore。HTTP 替身覆盖 lookup 的 in_progress、空登记、found/absent、权限不足及认证/schema/4 KiB 边界，走真实 aiohttp Router/Request/Response，无监听 socket；另须与候选 B+C 做真实 loopback 集成，替身不证明服务联通。

发布前必须核实：

- **网关 run 映射**：已读 C 的 `authorize_runtime`，并由插件 middleware 在原宿主 GET run 认证通过后导出 `task_id=run_id`、实际 session_id；工具发送相同 `X-Ollo-Task-ID/runtime.task_id`。宿主终端 task alias 不变；需沙箱外通过真实 POST/GET run 的测试及 B/C 联测。普通 `/v1/chat/completions` 无此 run 身份，本插件拒绝其确认执行。
- **确认 ID 告知模型**：C 原样转发 `Idempotency-Key=client_message_id`；插件按上文 canonical/legacy 宿主身份查询 UUID，hook 注入可信 confirmation_id，工具强制与之相等，并核对网关返回的 run_id。v0.22.1 不依赖 handler 返回早于模型的时序；新测试强制 canonical 执行先于 HTTP 返回，确认仍能取得精确 UUID。完成、取消或恢复后的旧请求不会通过重放重新授权，不从正文/历史或“最新确认”选 ID。
- **运行前去重**：此基线没有可靠的 cron pre-run veto，bridge 不用提示词假装阻止模型调用；允许重复只读生成，依赖网关每日事务唯一键防重复报告/推送。
- **失败通知联调**：替身验证 for_failure → 队列列 → delivery_kind 及原样重试；尚未验证真实网关 failure 落库后不置 agenda generated、固定兜底仍可成功。D12 只读设计会话提供的任务配置快照，部署窗口仍须核对 failure_deliver 和单目标/无附件条件。
- **D12 采集**：已读设计会话提供的真实任务/Skill 快照，非运行目录。新增脚本安装、Profile 路径、范围/超时/零项/500 项、名称双向映射与确认原值测试，待沙箱外跑；Swift typecheck 与静态检查通过。采集失败不会自动成为 for_failure，跨日 wake 的采集窗口也没有原业务日期输入，不能据此声称失败兜底和跨日展示已完成联调。
- **宿主真实挂接**：新 schema 列、队列恢复、既有 approval ContextVar 与 active agent 的跨 hook/工具线程核对、Profile scope、helper 签名与权限，均需沙箱外测试及候选环境确认；静态检查不能替代。
- **原生约束**：重现日程/提醒、重复外部 ID、同名清单等会明确拒绝受限写入；EventKit/iCloud 在预检后仍可能并发变化，不提供跨设备 CAS 或自动回滚。

2026-10-15 的 v0.22.1 身份适配及完整证据见 [session authority 修订记录](../../custom/records/2026-10-15-ollo-bridge-session-authority.md)。设计复核后插件全量 **525 passed / 0 failed / 14 skipped**；新增 canonical 测试用真实初始化、APIServerAdapter、TurnRunner、AIAgent 与注册的 hook/middleware，模型、Apple 和对端 HTTP 为替身。无 socket 的真实 aiohttp router 版本 11 项通过，TestClient/TestServer 版本 11 项因沙箱禁止监听而跳过，另 3 项为既有位置测试跳过。旧 legacy 8 项与新增存储故障/执行归属/GET 保留原响应的 13 项均通过；真实 loopback、网关重启及手机链路仍须沙箱外验收。safe worker 按宿主设计不加载插件，不属于 Ollo 的支持执行路径。
