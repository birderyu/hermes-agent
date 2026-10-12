# Ollo bridge 源码候选与宿主挂接

## 范围与提交

基于 `custom` / v0.21.6，先完成 [D1 语气开关归并](2026-10-11-ollo-mood-toggle.md)，再新增 `hermes-plugins/ollo-bridge/`。没有 checkout/修改 main，没有 rebase、提交、推送、部署或服务重启。旧三个插件目录保留，运行配置、Skill、任务提示与投递目标未修改。

合同来自 Ollo 仓库当前 bridge 0.2.0-draft 及共享样例；以联合方案开头用户决定为准：2/5 步一起发布、旧报告不导入、位置授权重新开启、只投 Ollo、验收使用专用测试事项。

## 实现

- `ollo` / `hermes_plus` live adapter 用 HTTP 投递可信 job/execution/start/date/trigger；保留 GTD 围栏，失败返回失败，standalone 拒绝。
- 三工具：报告读取、≤15 秒位置等待、网关授权的确认执行；API root 与压缩续接校验，其他渠道/分支/reset/委派拒绝。报告资料与身份追加本轮上下文，不改历史/system prefix；语气不做。
- 四个独立 bridge-key handler：wake 用宿主锁与既有 jobs.json 幂等入队，不解除暂停；capture 原文写「收件箱」；items 批量只读；interpret 无工具结构化辅助调用。HTTP 不持网关事务跨调用，不启用代理/重定向。
- 确认许可和业务回执都在网关；模型不能带另一组 changes/originals。确认回合通过宿主 hook/middleware 拦截其他工具，按真实 API run 绑定许可。依赖组一次 helper 调用、整组预检后一次 commit；未知结果不自动重新执行。
- 新增受限 EventKit helper：复用旧隔离审查副本已有 preflight/stage/commit 结构，调整 null=不改，增加只读报告采集。同次原值来自 Apple，不在投递/确认时补采；新增 Info.plist 用途说明。源码复用不代表运行副本曾安装。
- `docs/` 内放 GTD Skill 与四任务提示词候选，不安装，不改真实任务。拟配合只读 pre-run 采集与 `enabled_toolsets: []`，报告生成不获写工具。

完整文件清单、配置、部署与回滚见 [插件 README](../../hermes-plugins/ollo-bridge/README.md)。

## 设计复审后的核心范围

D1 由设计会话核验与线上逐字一致并确认通过。本次复审未改 D1 代码，也没有自行访问线上。

### 保留：幂等 live adapter 的有界投递重试

涉及 `cron/delivery_queue.py`、新增 `cron/delivery_retry.py` 和 `cron/scheduler_delivery.py` 的 12 行挂点，仅为一个问题；未移动 Telegram 路由、external worker 逻辑或普通超时日志。

原宿主行为（`HEAD` / v0.21.6 上的既有 custom）：

- `delivery_queue.py:228` 的 `claim_next` 只领取 `pending`（SQL 在 235 行）；`:258` 的 `_finish` 遇错误置为 `failed`。live adapter 返回失败经 router 冒泡后，队列就此终结，不存在自动重试。
- `delivery_queue.py:282` 的 `recover_abandoned` 把死 owner 的 `delivering` 置为 `unknown`，原意是避免重复发送；这是普通 adapter 应保留的策略。
- `scheduler_delivery.py:1992` 的 detached-worker 分支才会 `enqueue_and_wait`；`scheduler.py:3626` 的 `_launch_external_cron_worker` 在非 systemd 拓扑返回 False，macOS 进程内报告原本直接走 live adapter，失败后 standalone。仅补 queue 的失败重放不覆盖该入口。
- `scheduler_delivery.py:1519` 的已开始发送超时原本假定成功；幂等 adapter 的发送不能沿用这个结论。

补丁仅让真实 adapter 声明 `idempotent_delivery=True` 的单目标、无附件、非 relay 投递进入此分支。持久队列增加 `retry_safe/attempts/retry_at`；相同 execution/job/content 最多尝试 5 次、退避 1/5/30/120 秒，安全认证后的失败与 dead-owner 由原 drain 重放。进程内入口先入队并立即尝试；认证行的发送超时返回未确认，不用 standalone。任务中自报字段不能开启重放，未认证平台的 failed/unknown 终态与超时策略不变。

上游状态及退出条件已单独登记于 [补丁清单](../patches.md#未提交幂等-live-adapter-的有界投递重试)：v0.21.6 和本地 `upstream/main` 快照 `a62979dc3601` 都没有该能力；本次不 fetch、不查线上 issue、不提 PR，不能代表上游实时状态。上游有等价能力或消费者全部下线后可移除，先处理遗留队列行。

### 撤回：执行元数据核心改动与路由搬移

删除新增的 `cron/scheduler_delivery_metadata.py`，撤回 queue enqueue 的额外元数据冻结；`_live_route_metadata` 及其调用恢复原样。`tests/cron/test_cron_relay_dm_origin_user_id.py` 与 HEAD 逐字一致。

插件 `execution.py` 直接凭既有补丁透传的原始 execution_id 调用 `cron.executions.get_execution()`，核对 job_id 并读取 started_at/scheduled_instant。UUID 只在发往网关时格式化，不用规范化后的 ID 查宿主账本。账本缺失或不匹配就失败，不补造时间。

wake 本就把网关分配的 operation_id、business_date、manual_run_at 在调度锁内登记到既有 job。队列原来已经保存同 execution 的原始 `job_json`；插件以此精确匹配 job/execution/manual_run_at，固定调度排除旧 wake，不读后来变化的 jobs.json。因此无需另一张业务表、新网关登记接口或按开始时间近似匹配；网关已有 wake operation 身份，报告回传该 trigger_id。网关最终按业务日期去重仍负责固定兜底与 wake 的竞争。

### 撤回：API run 身份核心改动

`api_server_runs.py` 与 HEAD 逐字一致；删除新增的 `api_server_run_context.py`。现有 `_RunLaunch.approval_session_key` 已返回 run_id，`_run_agent_sync` 在拥有者 Profile 内绑定/清理 approval ContextVar；`_execute_run` 在提交 worker 前登记 `_active_run_agents[run_id]`。agent 进入本轮时已有 `_current_task_id/_current_turn_id`，hook/middleware 携带这两个 ID。

插件只调整对接：读 `_approval_session_key.get()`（不走 getter 的环境 fallback），精确核对 active agent 的 session/task/turn，加原 API root/压缩续接、Profile 与委派校验。确认 runtime.task_id 仍传实际 run_id，普通任务/会话 ID 不作为 run 身份；不是查“这个会话最新一次 run”。若 Ollo API run 缺少可信前置 hook 状态，或会话存储不可判定，工具执行关闭；正常其他会话不受影响。使用私有接口的升级兼容性由插件测试覆盖，不再扩大核心 API。

## 最终核心 diff 行数

相对现有 custom HEAD，未提交也未暂存：

| 文件 | 增/删 | diff --stat 变动行数 | 文件总行数 |
| --- | --- | --- | --- |
| `cron/scheduler_delivery.py` | +12 / -0 | 12 | 2143 |
| `cron/delivery_queue.py` | +36 / -17 | 53 | 434 |
| `cron/delivery_retry.py`（新文件） | +102 / -0 | 102 | 102 |
| 合计（核心代码） | +150 / -17 | 167 | — |

新增对应行为测试 `tests/cron/test_delivery_retry.py` 为 140 行。普通 `git diff --stat` 不显示未跟踪文件；新文件行数用 `git diff --no-index --stat /dev/null <file>` 补列，没有为统计而暂存。

## 冲突与验证

没有分支/合并操作或 Git 冲突。D1 保留 cce920898c 的 `_RunStream` 和异步 backlog 等待。D2 业务 handlers、Apple helper、合同与候选任务未因这次复审更改；插件改动仅为撤除两处核心补丁所需的身份/元数据对接及对应测试。

复审测试已编写：核心 retry 文件覆盖相同载荷、退避/上限、dead-owner、未认证行为、任务伪造开关、附件/多目标/relay 排除及超时/standalone；插件 test_cron 用真实临时账本/队列核对旧 wake 快照、固定兜底和 job/执行匹配，并用 HTTP 替身验证进程内重试；test_transport 拒绝伪造时间、缺账本/开始时间；test_runtime 经过真实 `_execute_run`/AIAgent/hooks/middleware 校验运行身份，并覆盖环境 fallback、并行 run/错误 turn/退休 run、缺 hook 和不可用会话库。全部仅使用替身和临时状态。

本次只作静态核对：23 个 Python 文件 AST 解析、`git diff --check`，以及已撤回文件与 HEAD 逐字对比。**未运行任何测试**，也未作官方基线测试对照；首轮的 JSON/schema/Swift typecheck 不代表本次运行验证。设计会话需在隔离 home/runtime、沙箱外运行：

```sh
export HERMES_RUNTIME_DIR="$HOME/.cache/hermes-agent-dev/tools"
export HERMES_HOME="$HOME/.cache/hermes-agent-dev/home"
source ./activate
scripts/run_tests.sh hermes-plugins/ --file-retries 0
scripts/run_tests.sh tests/cron/test_delivery_retry.py tests/cron/test_delivery_queue.py tests/cron/test_cron_relay_dm_origin_user_id.py --file-retries 0
```

静态维护检查另按 `python scripts/check --base v0.21.6` 执行。本次按设计要求保留原核心函数位置，不再为了文件行数门槛搬移无关路由；检查若触发行数/复杂度 ratchet，须由设计会话审定这个 12 行增量的处理方式，不以静态 AST 通过代替维护检查。

### 沙箱外首轮结果与测试收集修复

设计会话回传：使用隔离 `HERMES_RUNTIME_DIR/HERMES_HOME`、activate 和正式 runner，`tests/cron/` 全部通过（含新增 retry 测试）；ollo-inbox、ollo-location、ollo-conversation 通过。bridge 的五个测试文件均在 setup 阶段因 `attempted relative import with no known parent package` 失败，尚不能判定业务行为通过。

核对完整日志和该环境 pytest 9.1.1 源码，错误来自默认 `Package.setup()` 在 fixture 之前把带连字符目录的 `__init__.py` 裸导入。旧插件顶层无相对导入，或将相对导入延迟到 `register`，因此没有触发。bridge 五文件未调用旧 `load_plugin_module()`；其 `runtime` fixture 本来就调用真实 `PluginManager`，宿主 `_load_directory_module()` 已用包名、`submodule_search_locations` 和 `sys.modules` 正确建立相对导入上下文。

仅在 `hermes-plugins/conftest.py` 增加优先目录收集 hook，对精确的 `ollo-bridge` 路径返回 `pytest.Dir`，其余目录沿用默认收集。这样 pytest 收集该目录的 `test_*.py`，不再执行 `Package.setup()` 的提前导入；插件仍在隔离 fixture 内通过原宿主按包加载。没有修改运行时加载器、插件业务源码、旧插件辅助或核心补丁。单文件 runner 与目录入口均经过同一目录收集 hook；已有五个测试文件负责复测，无需复制一套插件加载器。

修复后本会话仅作 AST 与 `git diff --check` 静态检查，没有运行测试或 `--collect-only`；实际复测仍由设计会话在沙箱外执行。未修改 `custom/deploy.md` 和 `custom/records/2026-10-11-v0.21.6.md`，未提交、推送、部署或重启。

### 沙箱外第二轮结果与三项失败修复

设计会话回传：测试加载已修好，bridge 39/42 通过，其余插件全过。核对第二轮日志及源码后，修复范围仅为 bridge 的测试初始化、离线合同及维护说明；没有新增或调整核心补丁。

- `test_execute_run_exports_unique_host_identity_and_executes_only_receipted_groups` 在进入 claim/finish 前返回 forbidden，导致替身 `operations.outcomes` 为空。测试先导入 `run_agent`，其 `model_tools` 导入阶段调用 `discover_plugins()`；fixture 手工创建的 manager 此时尚未接入 `get_plugin_manager()`，另一 manager 会加载同一个插件目录、替换包子模块并重新注册同名工具。随后测试才绑定原 manager，造成 hook/middleware 与工具持有不同 Binding/ContextVar。将 manager 绑定提前到宿主导入之前，断言导入没有替换工具注册，继续检查真实分组回执、宿主 run/tool-call 身份，并增加工具结果非错误及三项 done 的断言。没有修改 Operations 替身，也没有放宽生产身份校验；实际运行闭环仍待复测。
- 两个 schedule 确认测试调用 `validate('P_GTDResult', ...)`，但旧离线包只沿 bridge 模型引用导出，漏掉了这个直接调用的公共入口。新增维护脚本 `contracts/export_schema.py`，从全部 bridge 模型及 `GTDResult`/`GTDSuggestion` 公共入口递归导出闭包；生成后由 96 个定义增为 97 个，仅新增 `P_GTDResult`，原有定义及两份源合同散列不变。其依赖 `P_UUID`、`P_GTDResultEntry` 原已包含，无需复制另一份定义。
- 新增 `test_offline_schema_references_and_runtime_roots_resolve`，检查离线整包和直接使用的公共入口，逐条通过 JSON Schema resolver 解析 `$ref`，并禁止外部引用。单查旧包内已有引用会漏检缺失的直接入口，因此两类都覆盖；原确认回放、unknown 不标 done、独立组继续执行的断言保持不变。

本轮只运行合同导出和静态检查（Python AST、JSON 引用/运行时入口清单、生成前后差异、`git diff --check`）；没有运行测试、collect-only 或官方基线对照。沙箱外 39/42 是修复前结果，不能记为修复后通过。指定的两份并行编辑文档未改，未提交、推送、访问运行 home 或重启。

## 未验证与发布门槛

### C 批衔接与 A2 capture 核对（实现已补齐，待沙箱外复测）

按 C 验收记录核对了网关 api/internal 路由、日程 core/storage 和 Hermes adapter，逐接口清单见 [衔接核对](../../hermes-plugins/ollo-bridge/docs/gateway-handoff.md)。身份衔接全部在插件中完成：HTTP middleware 捕获通过原 API key 校验的 Idempotency-Key UUID，在宿主新接受后按 run_id 临时绑定，worker 凭宿主身份查询，hook 注入本轮 confirmation_id，工具代码要求参数相等；GET run 在原宿主认证和所有权校验之后导出 `task_id=run_id` 与实际 session。原 ContextVar 透传方案的缺陷与本次修复见下节。不会重写宿主终端 task alias，也不新增核心补丁。确认未绑定的 409 仅重查只读详情；同 operation 多个显示组只执行一次，finish 使用实际许可索引，再映射显示结果。paused_job 已对齐 C 的 503 状态。

capture 创建改用 `ollo://capture/<request_id>` URL 标记，不写入标题/备注。原生 lookup 查询全部提醒清单和已完成事项，权限不足、不完整/超时、清单集合变化或重复匹配均 unavailable。`helper_process.py` 仅在内存登记活动 request_id，超时/取消后终止并回收进程；晚完成的 spawn 仍由保留的清理 task 接管，回收后移除登记，不保存完成历史。

设计会话最终指定 C2/A2 的分工：网关保证当前许可过期 + 30 秒后才调用 lookup，校验 unknown/operation/request/attempt 并原子应用结果。bridge 不新增许可查询或执行记录；同 request_id 活动时返回 HTTP 200 unavailable/in_progress，否则直接按 URL 查询，重启后空登记亦如此。已接入 `POST /v1/ollo-bridge/schedule/capture/lookup`，仅独立 bridge Bearer，严格 UUID 请求与 4 KiB 正文限制；不调用 claim/finish，不自行释放许可或重写。

原生 helper 在读取 stdin 前设置内核 `alarm(10)`，恢复 SIGALRM 默认终止行为并解屏蔽；10 秒自退不依赖父进程或主线程事件循环，阻塞输入/EventKit/commit 也受限。用户手动 `--authorize` 仅请求权限，不执行事项写入，保留交互等待。bridge 超时仍 kill + wait，不把超时当作未执行；capture 的父进程预算最多 6 秒。

已补测试：真实 POST/GET run 与 HTTP 网关替身贯穿 UUID/task_id/确认工具；请求身份缺失/伪造和 API key/owner 边界；重复 operation 与 run 绑定重查；lookup 三种结果、缺权限/超时不能 absent、URL 标记纯逻辑、终止与迟到 spawn 回收。最新补上真实 Router 的 in_progress（按 request_id，不按 attempt）、其他 request 可查询、回收后查询、重启空登记直接查询、认证/schema/4 KiB 边界；原生看门狗测试保持 stdin 打开，等待约 10 秒 SIGALRM 自退，不构造 EventStore。schema 从最新 A2 合同重新导出，含 99 个定义、189 处本地引用；lookup 共五份合成样例，插件 fixtures 共 39 份。

本轮未运行测试或 helper，仅 Python AST（23 个文件）/JSON 引用与入口核对、差异空白检查和 Swift `-typecheck`（通过，无 EventStore 执行）。上述新增行为测试仍待沙箱外运行，没有将静态检查记为测试通过。没有修改两份指定并行文档、网关源码或核心补丁，未提交、推送、部署、读真实 home 或重启。

### 沙箱外第四轮：190 项通过、确认 UUID 跨 worker 修复

设计会话回传 190 项通过、1 项失败：`test_execute_run_exports_unique_host_identity_and_executes_only_receipted_groups` 的 `checked_runs` 为 0。核对日志 `fork-fail-detail4.txt` / `fork-tests-plugins4.log`：首次模型替身先在“提示中应有确认 UUID”的断言失败，宿主将 AssertionError 当模型请求故障重试，第二次替身返回纯文本，导致没有工具调用，也未进入网关回查 run；不是网关替身字段过时。

根因在执行路径：`api_server_runs.py::_submit_api_worker` 使用 `loop.run_in_executor`，不复制请求 ContextVar；`_run_agent_sync` 只显式重绑 Profile、approval run_id 等宿主身份。D5/D6 的插件 REQUEST 因此在 worker 中为 None，确认 UUID 无法注入。现有 run_id/agent/session/turn 核对并未被跳过，而是缺少 UUID 后拒绝执行。

修复仅涉及插件 `run_context.py`、`identity.py`：删除错误的 REQUEST ContextVar 假设，新增实例内、加锁的短期 run→UUID 登记。认证仍由宿主 API key 校验；只登记新接受的 202、匹配 owner、仍活动的 run，任务结束撤销；room grant、无效 UUID、失败准入和幂等重放不能获得新能力。worker 仍核对确切 run/owner/adapter/Profile/live agent/session/task/turn。v0.21.6 handler 创建后台 task 后无 await 即返回，middleware 可在同一事件循环 tick 登记；不改核心、不包裹宿主 executor、不增加持久状态。

原端到端测试保留 `len(checked_runs) == 1`、真实 GET run 身份、两个 operation 回执及三项 done、幂等重放断言。模型提示断言移到替身外并收紧为 `confirmation_id=<UUID>`，避免被宿主重试吞掉。新增真实 executor（仅显式绑定宿主 Profile/run 身份）下同会话两个 run 的 UUID 隔离、完成撤权、owner/adapter 变化和重放拒绝测试。仅 AST 与差异空白静态检查；未运行测试、helper 或官方基线对照。190/1 是修复前外部结果，本次仍待沙箱外复测。指定两份并行文档未改，未提交或推送。

### A4 跨仓库对账：执行快照、失败通知与边界对齐

依据 Ollo `server/qa/step2-crosscheck-2026-10-11.md`、方案开头「跨仓库对账后的设计决定」和 A4 `contracts/bridge.openapi.yaml`。工作目录继续为 `custom` 分支；文档目录已由另一会话把 `fork/` 改名为 `custom/`，本轮沿用新路径，不改已有暂存改名。未提交、推送、变基或合并，无 Git 冲突。

**没有新增或扩大宿主补丁。** 对账指出的进程内路径，在当前工作区已有幂等 live retry 补丁下会先入原 delivery queue，再进行第一次 HTTP 投递：`scheduler_delivery.py::_deliver_result` 调用 `delivery_retry.queued_live_delivery`，后者保存当次 job/content/for_failure 并精确 drain 同一 execution。`claim_job_for_fire(return_job=True)` 返回的深拷贝保留本次 manual_run_at/external_trigger；队列 INSERT OR IGNORE 使重试不会换掉首次快照。插件不需要从后来变动的 jobs 配置补查，也不需要新宿主 metadata 模块。本轮开始/结束 SHA-256 对照确认原三处核心文件未变。

- `execution.py` 现在要求同 execution/job 的执行账本和原队列行同时存在；取 started_at/scheduled_instant，精确匹配当次 manual_run_at 与 external_trigger。wake 次日才开始仍输出原 trigger_id/业务日期；固定兜底按自身 occurrence，不继承旧 wake。缺失快照即返回失败，删除“缺快照时默认实际开始日”的静默退化。
- 可信失败标志不在 `_live_route_metadata` 中。`scheduler.py::_save_compose_deliver` 以 `for_failure=not d.success` 调用投递，`_deliver_crash_failure` 显式传 True；原队列独立保存此列并在 drain 时透传。插件从该列输出 A4 `delivery_kind: failure/report`，不信任正文、metadata 或 job 的同名字段。失败正文仍保留为原投递内容，展示「没有生成」、不消费 agenda 日账由网关负责，真实服务联调未做。
- location 对新鲜和缓存结果统一允许坐标时间领先本机最多 10 秒（含边界）。EventKit helper 自退改为 `alarm(7)`，短于网关 8 秒许可；bridge 的 kill/wait 与 request_id 活动登记不变。上节的 10 秒为 A2 当时实现，以本节 A4 的 7 秒为准。
- 离线合同重导出为 101 个定义、192 处本地引用；fixtures 共 42 份，增加 failure 投递/报告/回执。items title 对齐 4096 UTF-8 字节，capture 原文继续完整写入标题，超限拒绝、不截断。补 helper 纯逻辑长文本断言；不调用真实 EventStore。
- 同步 A4 的内部正文预算：items 640 KiB、interpret 4 MiB，lookup 4 KiB、wake/capture 16 KiB 不变。helper JSON 输入使用 UTF-8 而不把多字节 ID 膨胀为 ASCII 转义，100 个最长 ID 可放入其既有 256000 字节预算；未扩大宿主 HTTP 上限。

**行为测试已补，未运行。** `test_cron.py` 使用真实临时 jobs/executions/queue 和 live adapter、网关 HTTP 替身，覆盖 wake → 次日 due scan → fire claim、首次发送前 job 变动、正式/失败通知、HTTP 503 后相同载荷重试、调用字典变动及固定兜底；没有放宽既有断言。`test_transport.py` 覆盖缺队列拒绝、伪造类型无效、位置 10 秒边界和最大 Unicode 引用集合的 HTTP 正文预算。`test_apple_contract.py` 覆盖 513/4096 字节、中文/emoji 边界、超限拒绝、完整 capture、helper 输入预算、7 秒独立自退和 A4 合成样例；此前 schema 全引用可解析测试继续保留。

静态验证：23 个 Python 文件 AST 解析、42 份 JSON 解析、101 个定义/192 处引用及公共入口检查、`git diff --check` 均通过；Swift `-typecheck` 通过（只编译检查，没有运行 helper 或访问 Apple）。源合同散列见插件 contracts/README。按用户要求未跑 Python 测试、collect-only、helper 自退测试或官方基线对照；190/1 仍是本轮之前的外部结果。索引二进制 diff 与本轮开始一致，22 个已暂存改名未动；三个禁止编辑文档逐字未变。

部署仍需核对正式/失败任务都单投 `ollo:main`、无附件、非 relay，failure_deliver 为空继承或显式同目标；本轮没有读取真实任务。网关 failure 纯文字落库、不标 agenda generated、不 suppressed、之后兜底 report 正常保存，仍需双方联调。配置、部署、服务重启均未执行。

### 沙箱外第六轮：215/2、终态清理断言时序修复

设计会话回传插件 215 项通过、2 项失败，cron 1485 项全过；已核对 `fork-fail-detail6.txt` 和完整 `fork-tests-plugins6.log`。两个失败都是 `test_inprocess_wake_keeps_original_day_trigger_and_category_through_retry[False/True]` 在第二次 drain 成功返回后要求 job_json 仍等于完整任务快照。此前各项身份修复和 A4 边界测试已通过本轮外部测试，不能据此替代真实服务联调。

根因是测试断言的生命周期位置错误，队列行构造和 bridge 的活动快照来源均无误。`cron/delivery_queue.py::get_status` 直接 SELECT 当前行，并不隐藏活动载荷；`drain` 先领取为 delivering，在同步 send 返回后才 `_finish`。首次 HTTP 503 经 opt-in `retry_failed` 回到 pending，保留完整 job_json/content；第二次 HTTP 201 后 `_finish` 将其置 delivered，并在同一事务内调用 `_prune_terminal_unlocked` 写成 `job_json='{}', content=''`。此清理源自上游 `29e5172487`，是减少终态载荷保留的既有设计，原 `tests/cron/test_delivery_queue.py::test_pending_delivery_is_claimed_and_sent_once` 已明确断言这两字段为空。不能为迁就 bridge 测试取消清理或延长敏感载荷保存。

修复只改插件 `test_cron.py` 和说明文档：网关 HTTP 替身在返回 201 前只读采集真实队列行，返回后在测试主体检查采集结果，避免断言被 adapter 异常处理吞掉。保留完整 job_json 逐字相等断言，同时要求采集时状态为 delivering、content/for_failure 与首次失败后的 pending 行完全一致，两个 HTTP 载荷完全相等。成功后单独断言 delivered 及 job_json/content 已清理；再通过真实 `_deliver_result` 重入同 execution，断言命中终态去重且没有第三次 HTTP。跨日 trigger/business_date、伪造类型无效、固定兜底断言均保留，没有 mock 掉 get_status、队列清理或 bridge 身份读取。

不改 bridge 生产逻辑，不另建快照库，不新增或扩大宿主补丁；三个既有核心文件及其测试保持原样。仅做 Python AST 和差异空白静态检查，未运行测试、collect-only、helper 或官方基线对照；215/2 是修复前结果，修正后的两个参数仍待设计会话沙箱外复测。暂存区二进制 diff、22 个改名以及三个禁止编辑文档均与本轮开始一致；未提交、推送或访问运行 home。

没有访问真实 home、密钥、旧库/报告/位置、Apple store 或生产 API。没有部署与运行验收结果。

B/C 候选须联测 actual Hermes run ID 的映射与 confirmation_id 告知机制；本插件拒绝没有精确 run 身份的执行；宿主已有 approval ContextVar 与 active agent 私有映射仍需沙箱外验证。没有可靠 cron pre-run veto，不宣称模型生成前日去重，依赖网关最终每日唯一事务。helper 编译不证明系统权限或 iCloud 行为；任务原提示未读，候选须与原业务规则逐项审查。旧队列需按切换截点隔离，不能自动向新网关重放。联合验收前生产 schedule 不开启。

### A5：第二次对账复查的四处修复

依据 Ollo `server/qa/step2-crosscheck2-2026-10-11.md` 原 8/9、N1/N2，核对 A5 的 `bridge.openapi.yaml`、`openapi.yaml` 与网关 `core/errors.py`。本轮只改插件源码、测试和维护说明，没有分支/合并操作，没有提交，也未新增或扩大宿主补丁。

- **interpret 全程超时。** 原转换仅包住辅助模型调用，前面的报告 GET 和 Budget 耗尽直接漏出 read_timeout。现在外层 18 秒预算包住报告读取、模型等待及输出校验，任一 TimeoutError/read_timeout 或预算耗尽均为 504/interpret_timeout/true；其他永久读取错误保留合同语义。
- **完整错误表。** 原 HTTP 客户端与 BridgeError 各有小白名单，未知项替换 code 却保留原 HTTP 状态，还会把 not_configured 的 false 改成 true。现在统一按合同 29 个 code 决定状态与重试策略；expired 兼容旧事件 404。合法权限/配置/过期/超限错误不变成可重试；旧 Error 的未知策略省略 retryable。未知 code、缺失 code、非 JSON 上游诊断均隐藏为 503/dependency_unavailable/true，不回显私有诊断。错误配置本地也用合同的 not_configured。
- **实际 UTF-8 响应预算。** HTTP 请求、插件响应、工具文本、模型输入、helper 管道及摘要共用紧凑 UTF-8 JSON；原生 JSONSerialization 同样直出 UTF-8。report 字节计量改用实际 serializer，总预算不扩大。items 原生输出只含公开字段，完整 collect 不受影响；helper 输出与最终 HTTP 响应分别检查 2097152 字节上限，必要控制字符转义也计入。超限 413/payload_too_large/false，由网关/App 缩批重取，不截断标题、不丢结果、不返回部分 200。
- **长标题与生成时 originals。** 离线 schema 从 A5 完整重导出：事项、重复/冲突标题、update/merge/create_reminder、建议/候选标题、agenda、originals 全部同步 4096 UTF-8 字节约束。helper collect 保留完整标题；抽出其原值/公开字段投影供原生纯逻辑自检复用，比较原值和传给确认 helper 时均不截短。

新增 `test_http_contract.py`、`test_text_contract.py`，覆盖复查中的四个场景与边界：报告 HTTP 504/传输超时、模型超时、调用前/读取后/模型后预算耗尽、整段超时；合同全错误表与过期双状态、错误重试提示和非法 code/status 组合；100 条 1024 emoji 标题、2097152 前一字节/等于/超一字节、控制字符转义膨胀、HTTP 响应守卫；513 ASCII 及 4096 字节 ASCII/中文/emoji 在全部 GTD 标题字段通过、超一字节拒绝，长 originals 从报告到确认 helper 完整保留，128000 字节 emoji 报告按真实 UTF-8 预算发送。HTTP 替身使用生产 ClientSession 传入的 serializer，避免默认 JSON 编码掩盖问题；原生 `--self-test` 增加完整采集标题、变化核对及 UTF-8 输出断言。未放宽此前任何断言。

实现时完成静态验证：25 个 Python 文件 AST、43 份 JSON（schema + 42 样例）、101 个定义/192 处本地引用与公共入口检查、源合同散列核对、差异检查，以及 Swift `-typecheck`。随后设计会话在沙箱外用隔离 HERMES_RUNTIME_DIR/HERMES_HOME、activate 和正式 runner 完成 A5 测试并回传：**`hermes-plugins` 286 passed / 0 failed；`tests/cron` 1485 passed / 11 skipped**，11 项为上游既有跳过。该结果覆盖本节 A5 新增用例和上节两个终态断言时序修正，替代此前“尚未运行 Python 测试、待复测”的状态；不是本会话在沙箱内运行，也不代表真实服务/Apple/iCloud 联合验收。未另作官方基线全量对照。合同散列已更新在插件 contracts/README。

没有修改宿主核心文件或测试加载器；原来的单项通用核心补丁保持不变。不动暂存区及 22 个改名，不修改 `custom/deploy.md`、`custom/README.md`、`custom/records/2026-10-11-v0.21.6.md`。没有访问真实 home、密钥、Apple store 或服务，没有改任务、部署、重启、提交或推送；真实权限/iCloud 与网关联合验收仍待授权窗口完成。

### 第三轮对账 N3：字段字节超限的错误分类

依据设计会话提供的 `codex-crosscheck3.last.md` N3，核对网关 `core/validation.py` 与 bridge `protocol.py::validate`。旧代码仅用 `.validate()` 抛出首个 schema 错误，并统一映射 400；同一字段可能先报 maxLength 再报 x-max-utf8-bytes，组合 schema 还把实际字节错误放在 ValidationError.context 中，因此整包未超限的 4097 字节 capture text/current_suggestion.title 被误报为 invalid_request。

现在用 `iter_errors()` 遍历校验错误，递归检查 context；任一实际失败包含 x-max-utf8-bytes 即返回 **413/payload_too_large/false**，没有字节错误才返回 400/invalid_request/false。只遍历失败的校验结果，不直接扫描 schema，避免把已被其他 anyOf/oneOf 分支接受的合法值误判。HTTP handler 沿用原 BridgeError 出口；interpret 的模型输出校验仍捕获 BridgeError 并转成 503/invalid_model_output/true，不把模型错误归咎于请求。

新增 `test_schema_errors.py`：真实 HTTP handler 覆盖 capture text、interpret text/current_suggestion/事项/建议/关联提示/候选标题/各修改分支的 4097 UTF-8 字节输入，断言整包仍在预算内、返回 413 且未读取报告/领取许可/启动 helper。items 的 ID/数量、interpret 的引用 ID/notes/list 等只有字符或结构约束的错误仍为 400；ItemID 合同没有 x-max-utf8-bytes，不新增限制。真实 jsonschema 测试覆盖 oneOf/anyOf/allOf、多层组合、先出现其他错误、精确字节边界，以及合法替代分支。原长标题超限测试加强为精确 413/false；HTTP 模型替身验证超长标题、类型/空标题错误仍为 invalid_model_output。

N3 修改与新增测试尚待设计会话沙箱外复测；上述 286/0 和 1485/11 是 **N3 修复前的 A5 结果**。本会话只作 Python AST 与差异静态检查，不运行测试或 collect-only；没有修改宿主核心、合同内容或 helper，也没有扩大补丁。暂存区及禁止编辑文件保持本轮开始时状态，未提交、推送或访问运行 home。

### D12：部署候选的只读采集、Apple 名称对应表与四任务提示词

只读参考设计会话提供的 `scratchpad/gtd-review/original-jobs.md` 与 `gtd-SKILL.md`，没有访问运行 home；原 Skill 全文未复制进仓库。本节取代早期“未读任务原提示”的待办状态，但只证明已核对该快照，不代表检查或修改了运行任务。工作日 agenda `d83281b75902`、周末 agenda `8211bb91402a`、工作日 inbox `12d6987e974e`、周日 review `f9a5dd70ea34` 的 ID/时刻/模型/提供方/repeat 保留，单投 Ollo 等仅写成部署候选。

**实现：** 新增 stdlib-only `apple_config.py`、`collector.py`，以及可安装到 owning home/scripts 根目录的 `ollo_collect.py` 共用加载器、agenda/inbox/review 三个薄入口。加载器由实际安装路径定位同 home 的完整插件，直接加载 collector 包，不执行注册、不读 bridge key、不发现其他插件。collector 只读 bridge.json，构造固定 collect 请求，只调用一次用户配置的绝对 helper_path；6 秒超时由 subprocess.run kill/reap，helper 原有独立 7 秒自退继续有效。成功 stdout 只写一份 UTF-8 JSON；零项也非空，避免宿主 wake gate 静默跳过。非零退出、超时、权限/名称/结果错误均 exit 1，固定失败原因写 stderr，不输出空结果冒充成功，也不回显事项或路径。输出超过 16 MiB 明确失败。

**范围：** `collection` 为三种任务的逻辑 lists/calendars 范围，默认 agenda/review 为收件箱/工作/生活/家庭/等待/长期六清单和工作/生活/家庭三日历，inbox 仅收件箱、不读日历也不要求日历权限。agenda 读上海当日 00:00 至次日 00:00 日历，review 读采集开始时刻起七天；提醒事项均不加日期限制，保留全部超期、未来到期与无日期候选。helper 每源一次读取，先过滤已完成提醒，再计算未完成提醒+日历事件总数，500 项通过、501 项明确 collection_limit_exceeded，既不截断也不假空。items 与 originals 同批生成并保留完整字段，无 modified_at 时不伪造快照。

**名称对应表：** 可选 `apple.lists`/`apple.calendars` 默认同名，局部覆盖后仍须各自一一对应，拒绝未知逻辑名、重复/空实际名和 Unicode 等价冲突，插件启动与 collector 均校验。native helper 再校验一次，避免其他入口传入歧义表。capture 写 lists[收件箱]，apply 所有 list/calendar 目标解析同一对应表；collect/items/originals 将已映射实际名称转换回逻辑名，表外实际名原样输出。确认前读取快照用同一映射再比较 originals。lookup 仍扫描全部提醒清单，不能因切换测试表而漏掉既有 capture URL。验收用「Ollo 测试·…」，结束时撤表并重载；不能让旧测试报告/确认继续授权生产操作。

**宿主源码核验与限制：** 路径/解释器/参数/环境见 `cron/scheduler_script.py::_resolve_script_path/_script_argv/_run_job_script`，生命周期扫描见 `cron/lifecycle_guard.py`，一次预执行与 prompt 注入见 scheduler.py 和 scheduler_prompt.py。script 仅路径，不解析命令行参数，相对 home/scripts 解析且软链接不能越出该目录；.py 用宿主选择的 Python。环境经 build_subprocess_env 过滤并绑定所属 Profile，但没有 job/run/原调度日期输入，故用三个固定入口区分种类。cwd 为 workdir 或脚本目录；宿主总超时默认 3600 秒，可由既有环境/cron 配置调整，取消/超时会终止进程树。成功 stdout 缓存并只注入一次；enabled_toolsets=[] 明确不合并 MCP 工具。

两个上线前必须说明的边界没有新增核心补丁绕过：普通 agent cron 脚本失败只生成 Script Error 上下文，模型仍运行，**不会自动令执行失败或设置 for_failure**；模型正常返回“未生成”时仍是 report，可能消耗网关 agenda 日账。候选提示词只能禁止虚假空卡片，不能保证失败通知和固定兜底语义。另因脚本不收到原 wake 日期，实际采集日与外层可信 business_date 在跨午夜延迟开始时可能不同。README 和提示词文档明确要求联调/处置方案，不能宣称这两条已接通。no_agent 虽能传播脚本失败，但不能完成此次模型生成报告的需求，未用它偷换配置。

**文档：** 重写 `docs/cron-prompts-candidate.md` 为四段完整可审阅提示词，合并原业务规则和 Ollo v1 围栏/生成时 originals/卡片确认合同；保留 1–3、1–2、每周最多 3 重点，周末生活家庭优先、无日期候选、全部超期、不说新增、0 项也发、允许延后、信用卡偏移、预约不等于完成及不自动修改。去掉原消息平台、原采集命令与工具指令。新增 `docs/gtd-skill-update-candidate.md` 只列现行 Skill 行号与差异，未收录全文；同步 v1 Skill 候选和 README 的配置、脚本安装、对应表、部署步骤及上述边界。实际任务、Skill、配置均未改。

**测试与验证：** 新增 `test_collection.py` 与 `test_apple_config.py`，覆盖默认/自定义逻辑范围、上海日期与七天窗口、0/500/501、重复/不完整结果、stdout 一份 UTF-8 JSON、权限/超时/超限失败、配置拒绝重复映射与覆盖默认冲突、helper 请求不受调用方伪造映射影响。安装测试复制真实脚本和插件到临时 home、删除合成 key，使用替身 helper 经真实宿主路径/生命周期扫描执行，验证无额外 argv、一次 stdout 注入、零项仍唤醒模型、显式空工具集及 Script Error 语义。Swift 既有安全 --self-test 增加双向映射、表外名称、逻辑 originals/确认变化比较、先过滤完成项再计限额和重复 ID 断言；不构造 EventStore。既有 Python helper 请求精确断言补入 apple 字段，没有放宽原断言。

本轮静态验证通过：**34 个 Python 文件 AST、43 份 JSON、192 处离线引用、README JSON 示例与四段 prompt 结构、插件源码空白检查、git diff --check；Swift -typecheck 通过**（module cache 在临时目录）。未运行 Python 测试、collect-only、helper 自检或真实 Apple 调用，也未跑完整 scripts/check/官方基线对照；N3/D12 行为测试待设计会话沙箱外正式 runner。此前 A5 的 286/0 与 cron 1485/11 不延用为本轮通过结果。

全程在 custom，无提交/推送/变基；没有新增或扩大宿主补丁，现有三个核心文件与共用测试加载器和本轮开始时逐字相同。暂存区二进制 diff 未变；本轮开始时前述文档改名已由其他会话提交，未操作索引。`custom/deploy.md`、`custom/README.md`、`custom/records/2026-10-11-v0.21.6.md` 与本轮开始时逐字一致。没有访问运行 home/密钥、真实 Apple store 或服务，没有部署、重启或改真实任务。

### D13：区分写入授权来源，收窄 config.yaml 部署差异

设计会话指出 D12 的现行 gtd 差异稿将所有 Ollo 日程写入都限制为卡片确认，会回归用户直接请求的 Apple Skill 能力。本轮仅改文档和候选：现行 gtd 差异稿、v1 Skill、四段完整提示词和 README 统一区分两类来源。定时报告（今日安排、清理收件箱、每周回顾）提出的建议，仍只能经 Ollo 卡片确认后由 execute_gtd_confirmation 执行，普通文字“确认”不授权这类修改；用户在对话内直接请求修改，保留先列出确切变更、得到对话明确确认、再用 apple-reminders / apple-calendar Skill 执行的现行规则。只去除旧平台专属的文字确认/投递措辞，不取消直接请求的对话确认。卡片回执围栏、v1 操作集合限制和 cron 空工具集也明确限定作用范围。

以用户提供的当前配置为准，没有读取运行配置。README 将 plugins.enabled 的唯一必需变更写为：保留 bot-handoff、homeassistant、orca-status，移除三个旧 Ollo 插件，新增 ollo-bridge。旧 plugins.entries.ollo-location 可删除清理，**不必改名或新增 ollo-bridge entry**：源码 PluginContext.register_tool 默认不 override，plugin_capability_granted 缺省不授予 tools.override，bridge 未申请该能力；allow_tool_override: false 不是工具白名单。现有 photon/hermes_plus/ollo 平台 enabled 无 bridge 接入增量，旧消息渠道停用属于其他既定部署事项。timezone 在无其他覆盖时跟随本机时区，用户已确认 Asia/Shanghai，因此无需新增；显式固定仅可选。

撤回 README 的“原工具白名单改成 ollo_bridge”：这并非另一处已知配置，而是将注册工具组名误写成必要部署项。核对 integration.py::register、gateway/platforms/api_server.py::_create_agent、hermes_cli/tools_config.py::_get_platform_tools；Ollo App 对话走 api_server，当前没有保存的平台白名单，无需新增 platform_toolsets.api_server/ollo，更不能用 [ollo_bridge] 替换既有工具选择。四个任务的 enabled_toolsets=[] 保留且只作用于报告模型。核对 identity.py 与 integration.py 的既有确认标记逻辑后，本轮无需代码修改。

验证仅做文档差异、空白与引用目标检查；无代码或测试改动，不运行 Python 测试、Swift 或完整 scripts/check。现有源码、三个受保护文档及暂存区与 D13 开始时一致；全程在 custom，未提交/推送、未访问运行 home/密钥、未改真实任务/Skill/config.yaml，也未部署或重启。此前 N3/D12 待复测及 D12 脚本失败/跨日边界状态不变。

### D14（2026-10-12）：A6 日期合同与分钟精度兼容

依据 Ollo `server/qa/contract-formats-a6-c13-2026-10-12.md` 和当前两份源合同同步。线上根因与错误数量来自设计会话，本轮未读取线上报告或运行环境。旧公共 ISODateTime 仅靠可选 `format: date-time`，缺少实现时纯日期同时命中 GTDDate 的两个 oneOf 分支。当前 bridge 的 `protocol.py` 已自行用标准库注册 date/date-time/uuid 检查器，因此不能把网关的缺包复现直接当成本插件运行路径的实测；离线 schema 及交给辅助模型的派生 schema 仍必须独立满足 A6。

**合同：** 使用既有 `contracts/export_schema.py` 从 Ollo 源文件完整重导出 `contracts/schema.json`，仍为 101 个定义、192 处本地引用。ISODate 增加形状与真实日历日期 pattern；ISODateTime 改用含时区的完整 pattern，接受分钟、秒、小数秒，移除要求秒必填的 `format: date-time`。GTDDate 保留 oneOf，但在所有格式检查关闭时也互斥。`protocol.schema()` 的各入口从该包提取引用闭包，无第二份手写派生定义。源合同散列与同步说明更新到 contracts/README；本次完整导出也带入源合同关于 agenda 成功日账和展示正文去围栏的说明，未在 bridge 中增加对应网关逻辑。

**时间解析：** `protocol.py` 的 stdlib 检查器、`execution.py::business_day` 和 `collector.py::validate_collection` 使用 `datetime.fromisoformat`，支持带时区的分钟精度，无需修改 Python 生产逻辑。EventKit `parseDate` 在进入原 ISO8601DateFormatter 前，仅对完整的 `YYYY-MM-DDTHH:MMZ/±HH:MM` 输入补 `:00`；无时区输入不参与补秒。日期、秒、小数秒路径保留，iso/fractional 输出格式器未变。提醒 due/follow_up/alarm、日历 start/end、采集窗口与 originals 前置比较共用这一解析入口；7 秒自退逻辑不变。

**回归测试已补，尚未运行：** 新增 `test_datetime_contract.py`，显式构造仅 date/uuid 与完全无 format 两种 FormatChecker，并断言没有 date-time；替换 bridge 自有检查器以避免其掩盖 schema 依赖。整包和按入口派生的 schema 均覆盖 GTDReport（含 agenda/originals）、GTDSuggestion、GTDConfirmation、ScheduleItem，验证纯日期只命中 ISODate、带时区时间只命中 ISODateTime、无时区时间拒绝。覆盖分钟/秒/小数秒、闰年/世纪年、真实日期、年范围、时区偏移、24 点/闰秒/空白/大小写及 64 字符边界；并覆盖执行业务日期换算与采集时间原文保留。Swift 的既有纯逻辑 `--self-test` 增加分钟精度的正/负偏移与 Z、components、originals 等价/冲突比较、无时区拒绝及输出格式断言，由既有 macOS 原生合同测试入口运行，不创建 EventStore。

**本轮验证：** 35 个 Python 文件 AST、43 份 JSON 解析、101 个定义/192 处本地引用解析、导出结果与当前 A6 源文件逐字一致检查、`git diff --check` 均通过；`xcrun swiftc -module-cache-path <临时目录> -typecheck` 通过。使用已有隔离开发解释器执行导出，没有安装任何包或修改 pm 环境。按此前约定，未运行 Python 测试、collect-only、helper 自检、完整 scripts/check 或官方基线对照，行为验证留给设计会话沙箱外 runner；不借用 Ollo QA 的 1670 passed 作为 Hermes 测试结果。

没有新增或扩大宿主补丁，Hermes 核心、`pyproject.toml`、`uv.lock` 均未改。只在 custom 工作区修改插件与本记录；暂存区及三个禁止编辑文档与 D14 开始时一致，其他会话已有的记录改动保留。未访问运行 home、密钥、真实 Apple store 或服务，未部署、重启、提交或推送；本记录不代表线上故障已通过部署消除。

### D14 沙箱外复测：399/2，修正 alarm 的测试类型假设

设计会话通过 `scripts/run_tests.sh hermes-plugins/` 回传 **399 passed、2 failed**；已核对 `fork-plugins-D14.log`。两个失败分别是日期测试的 stdlib-formats/no-formats 参数，均因将 `GTDConfirmation.changes[2].alarm` 改为纯日期后未抛 BridgeError。该元素是 `op=create_reminder`：源合同 `openapi.yaml` 的 `GTDConfirmedChange → GTDChange.oneOf[create_reminder].properties.alarm` 明确定义为 `anyOf: [GTDDate, null]`；GTDDate 又是 ISODate/ISODateTime 的 oneOf。离线 schema 与此一致，合法纯日期应通过；这是测试把 alarm 错当 ISODateTime，不是 A6 合同遗漏。

本轮只修测试与本记录。公共确认样例增加 `update_event`，将 timestamp_path 改为 `changes[3].start`，其合同为 ISODateTime，继续断言纯日期和无时区时间均拒绝。新增 alarm 正反例，在两种无 date-time 检查器配置下同时验证整包与派生 schema：纯日期、带时区的分钟/小数秒时间、null 和省略均通过；无时区分钟/秒时间及非法日历日期拒绝。未修改 Ollo 合同、离线 schema、生产校验器或 helper，也未删除原有拒绝断言。Python AST 与差异空白检查通过；按约定未在沙箱内运行测试，399/2 是本次修正前的外部结果，修正后待复测。暂存区、禁止编辑文档及其他既有改动保持不变；未提交、推送、部署或访问运行 home。
