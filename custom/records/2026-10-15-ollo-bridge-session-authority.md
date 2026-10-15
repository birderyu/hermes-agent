# Ollo bridge 适配 v0.22.1 session authority

日期：2026-10-15。状态：开发源码完成，沙箱外测试通过；未部署、未重启（随 v0.22.1 一起部署）。

本记录同时作为交付报告；任务提到 `-o` 输出，但消息中没有提供另一个目标路径。基线现场核对为 `custom/HEAD = 1fc6fb27e50b5d1115b80f4a8f1934daff0512a0`，`main = v0.22.1^{commit} = 297552a9d646119871391d202509822cd86330df`。升级记录 `2026-10-15-v0.22.1.md` 的既有未提交改动保留，未编辑；`custom/patches.md` 未改。没有改 Hermes 核心、其他插件或 Ollo 仓库，没有访问运行 home 或读取真实密钥。测试只使用临时合成配置和凭据。

## 问题与复现

v0.22.1 的常驻网关先把 API 请求提交到 session authority，API `_execute_run` 只观察 canonical turn，不再创建自己的 agent。旧 bridge 从 approval ContextVar 取 run_id，再查 `_active_run_agents`；canonical 执行器把这个 ContextVar 设为会话路由，所以原查找失败。

证据：`gateway/platforms/api_server_runs.py:667`、`:821`、`:1042`、`:1069`；`gateway/run_turn_runner.py:886`。历史 `git log -p -S 'active_run_id=run_id'` 定位到 `f2d9cea38e`（admit API runs before acknowledging execution），确认这是宿主有意统一执行归属，插件应适配，不能退回第二套 agent。

先加入真实 canonical 集成测试，在未修复插件上运行：4 项中 3 过、1 失败。失败发生在确认 UUID 未进入本轮 prompt，实际工具返回 `forbidden`，不是导入或模型替身错误。接通身份后，压缩续接测试再复现 GET 状态为 `root`、工具为 `tip` 的差异；该投影也已在插件内修复。

## 宿主身份来源及可信性

选择的对应关系是 **正在执行的 authority claim → admission.request_id → POST 返回的 run_id**，同时用该 claim 接管的活 agent 核对本次工具的 session/task/turn。

| 宿主事实 | 源码位置 | 插件如何使用 |
| --- | --- | --- |
| POST 自行生成 run_id，记录认证 owner，并在 admission 前保留幂等 UUID/请求指纹/run_id | `gateway/platforms/api_server_runs.py:644`、`:763`、`:795` | 模型参数和正文不能提供这些绑定 |
| admission 传入 `active_run_id=run_id` 与 `run_owner_scope` | `gateway/platforms/api_server_runs.py:678` | 区别于任意 chat/WS 回合 |
| 宿主把 active_run_id 写为 admission 的 request_id，同 API request_id 不能指向两个会话 | `gateway/session_api_turn.py:217`、`:240`、`:265` | 读取精确 admission，绝不按“最新 run”或 session_id 猜测 |
| claim 提交后写 execution epoch/generation/admission_id；执行结束立即清除 executing | `gateway/session_authority.py:830`、`:885` | 旧 agent、完成后的 ContextVar 副本都不能继续获得身份 |
| TurnRunner 为该 generation 接管真实 agent；agent 自行生成并保存 task/turn 身份 | `gateway/run_turn_runner.py:452`；`gateway/session_authority.py:697`；`agent/turn_context.py:572` | 每次 hook/工具核对精确 session/task/turn，不只比较会话路由 |
| `api_execution` 由宿主为 canonical 执行绑定，并保留实际 adapter | `gateway/session_ingress.py:223`、`:239`；`gateway/session_api_turn.py:437` | 确保本次是同一个 API adapter 的执行；非 API 上下文不接受 |
| 当前 home 选择 authority；执行器和工具线程传播 ContextVar | `gateway/session_authorities.py:123`；`gateway/run.py:4048`；`tools/thread_context.py:43` | 与插件 home 和 Ollo 会话谱系一起核对，排除其他 Profile 和委派 |

实现见 `hermes-plugins/ollo-bridge/authority_identity.py:5`、`:38` 和 `identity.py:63`。approval key 只用于找到宿主 live route；只有 epoch、generation、adopted agent 和精确 turn 全部吻合，且 admission 为 `started`、`principal_id=api`、target/owner scope 一致，才返回 admission.request_id。session key 和 session_id 从未代替 run_id。

同一会话可复用 agent 和 task_id，但每回合 turn_id 不同，每个 claim 的 generation/admission_id 不同；前回合结束后 executing 被清除。测试保留旧 ContextVar 副本，并在结束及后续普通回合检查旧身份失效。普通回合的工具也不能使用历史确认 ID。

legacy 保留 `_approval_session_key` → `_active_run_agents[run_id]` → 精确 session/task/turn 的原路径。两条路径继续通过 `start`、`request_id`、`confirmation_id`、`runtime(confirmation=True)` 和工具 middleware 使用同一套检查；非 Ollo 谱系、其他 Profile、其他平台、委派均没有确认能力。`integration.py:125` 仍将模型参数与可信 UUID 比较，不相等为 `403/forbidden`；未改变工具合同。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `hermes-plugins/ollo-bridge/authority_identity.py` | 新增只读 canonical claim/agent/admission 校验，不修改宿主运行状态 |
| `hermes-plugins/ollo-bridge/identity.py` | canonical 与 legacy 分流；hook/存储失效时保守拦截活跃 Ollo 回合的工具 |
| `hermes-plugins/ollo-bridge/run_context.py` | 新请求 pending 绑定覆盖异步 admission 窗口；任务结束撤权；GET 投影解析压缩续接 |
| `hermes-plugins/ollo-bridge/test_session_authority.py` | 真实 authority、API、TurnRunner、AIAgent 集成测试，router 与 TestClient/TestServer 两种入口 |
| `hermes-plugins/ollo-bridge/test_identity_availability.py` | 全局 hook 的执行归属边界、存储缺失/SQLite 错误及 GET 原响应回归测试 |
| `hermes-plugins/ollo-bridge/README.md` | 两条身份路径、时序、投影、测试证据与限制 |
| `custom/records/2026-10-15-ollo-bridge-session-authority.md` | 本报告 |
| `custom/records/README.md` | 追加索引 |

未修改 `integration.py`、合同 schema、父 `conftest.py` 或旧 legacy 测试。无新增核心补丁。

## 升级审查第 6 节逐项核对

| 项目 | 结论 |
| --- | --- |
| `_RunLaunch` / `_execute_run` | `admission=None` 仍兼容 legacy。canonical 不填 active agent，改为上述 authority 身份来源。无需改核心 |
| `remember()` 时序 | 不能沿用 v0.21.6 注释。已有 FIFO drain 可能在新 admission 提交后、HTTP await 返回前 claim 新回合。`run_context.py:17` 先为通过认证、尚无宿主 reservation 的 UUID 保存短期候选；`:50` 仅在宿主 store 的 owner/UUID/指纹已精确对应当前 run 时可读。返回后 `remember` 接管并绑定 task；finally 删除 pending。测试强制整个 canonical 回合早于 admission handler 返回，仍成功执行确认 |
| `_active_run_tasks` / `_run_owners` / replayed | owner 在 admission 前记录（宿主 `:764`），task 在 admission 后创建（`:830`），仍存在但不能假定执行开始时已有 task。`replayed` 仍由宿主返回并用于拒绝重复登记。已有 reservation 不建立 pending；活动中及结束后的重放均不产生第二次执行或许可。取消 observer 会撤销 task 绑定，canonical 即使继续也不能借此重授确认 |
| GET run 投影 | 原 task_id=run_id 的逻辑可保留，session_id 需要修复。`api_server_authority_runs.py:51` 的投影使用逻辑 root；插件解析 `resolve_resume_session_id` 后与工具的物理压缩续接一致。确认测试模拟 Ollo `authorize_runtime`，真正反查 GET 并比较 run/session/task。复核补充：数据库缺失或读取/解析失败时保持宿主原响应，不能部分改写或引入 500 |
| `_ensure_session_db` / `bound()` | `api_server.py:1837`、`api_server_store.py:5` 借用当前 home 的 authority.db。真实初始化后 Ollo root→tip 返回 True；有 runner 但 authority 未就绪时返回 None。复核收窄：此时只有本 adapter 的 API 执行拦截通用工具，cron/其他平台/其他 adapter 放行通用工具；Ollo 专用能力继续拒绝。其他 home 返回 False，不读另一 Profile 的库 |
| delivery queue / drain | 原队列快照与发送阶段仍适用。成功后清理敏感载荷，终态不可作为历史身份来源。既有插件队列/HTTP 重试测试及核心 queue/retry 测试通过；未改队列、重试或 incident 回写 |
| `mark_job_run(execution_id=…)` / canonical_completions | `cron/jobs.py:2460` 对已完成 execution 去重，防止 canonical 恢复重复推进 job；`scheduler_authority.py:23` 保留仍可恢复的 completion。bridge 的 wake 仍在宿主 jobs 锁内保存 external_trigger，报告仍读 executions + delivery queue 的精确快照，不从 canonical_completions 认领报告。无需更改；核心 completion retention 测试通过。真实 wake、失败通知和恢复投递顺序仍待网关验收 |
| auxiliary client | interpret 仍走 `ctx.llm.acomplete_structured` 和同一超时预算；异步校验转线程不改变 bridge 合同。插件超时、非法输出、schema 测试通过。真实 provider/Gemini 行为未验证 |
| registry session overlay | `tools/registry.py:537` 的隐式 scope 会考虑当前 session overlay。旧 `test_runtime` 导入前后 entry 身份断言和新 canonical 注册工具调用都通过，没有删掉/放宽断言，也没有修改注册方法 |
| safe worker / 插件加载 | 宿主 `plugins.py:1280`、`plugins_dispatch.py:256`、`:642` 明确跳过插件发现及 hook/middleware。普通 API canonical source 不采用本地 safe worker policy（`session_managed_worker.py:36`）。不绕过此设计，不声称 safe worker 支持 Ollo；相关核心端到端测试因监听被拒而未验证 |
| AIAgent 参数 | 现有参数仍可用，新测试创建真实 AIAgent，仅替换模型传输/元数据及标题生成；不替换 run_conversation、TurnRunner 或 authority 的执行 |
| BasePlatformAdapter 输入/取消 | bridge 的主要入口仍为 HTTP 与投递。API queued stop 经真实 authority 取消后不能重放确认，非 API 身份拒绝。其他渠道真实输入/取消和手机推送不由此测试证明 |

## 设计复核后的修正

1. **全局 hook 的拦截范围**：初版 `bound is None` 无条件拦截会误伤非 API 执行，已修复。`identity.py:84` 的 `owns_api_execution()` 只读宿主 ContextVar：canonical 必须是同一 adapter；没有 canonical 上下文时，legacy approval key 必须指向本 adapter 的活 agent。`guard()` 在没有可信 state、谱系不是明确 False 且上述归属成立时，才拦截通用工具。此归属检查只决定是否拦截，不签发 run_id 或确认许可。测试通过真实注册的 `pre_tool_call` 分别覆盖 cron、微信、其他 adapter、当前 canonical/legacy，以及移除 legacy 活 agent 后的行为；数据库返回 None 和抛 `sqlite3.OperationalError` 两种故障均覆盖。
2. **GET 投影退化**：`run_context.py:126` 在第二次取库和解析续接时处理 None、SQLite/OSError；所有投影字段算完才改写 body。测试刻意让谱系检查先成功，再让取库返回 None、抛 SQLite 错误或解析抛 SQLite 错误；三种情况下均保留宿主原 200 状态和完整 JSON。
3. **legacy 与 runner/authority 组合**：没有删除 `current_run_id()` 的 `bound(...) is True` 前提。正常生产启动顺序是 `gateway/run_bootstrap.py:715` 建 runner、`:717` await runtime 初始化、`:773` 开 runtime API、`:789` 启动 runner/adapters；`gateway/run_runtime.py:119` 初始化每个 Profile，`:121`–`:123` 主 Profile 失败会抛出并中止启动，副 Profile 失败会停用；`:50`、`:55` 完成 authority 构造与注册。因此正常已开放 API 的主 Profile 不存在“只有 runner、没有 authority”的 legacy 服务状态。对象构造/故障/测试可人为形成此组合，不能宣称所有时刻绝不可能：`api_server.py:1840` 与 `api_server_store.py:9` 此时明确不给 SessionDB，插件也不能绕过它从其他库补造谱系。新增断言在真实 runner/authority fixture 中移除 authority、补齐 legacy 活 agent/key，仍拿不到 run_id，且只拦本 adapter 的 API 工具。无 runner 的独立 legacy adapter 走 `api_server.py:1844` 的原 SessionDB，旧 8 项测试继续通过。

本轮先运行新增回归测试复现问题：**2 passed / 11 failed**；其中 legacy 失败是活 agent 移除后仍误拦，GET 三项分别复现 AttributeError/SQLite 异常。修复后结果见下。未改核心、旧 legacy 测试或升级会话记录。

## 测试命令与结果

用户指定的启动命令先执行到 `source ./activate`：uv 尝试使用隔离开发目录以外的缓存，因 `Operation not permitted` 失败；没有继续使用错误环境。随后采用已经安装、包含 pytest 且与本 checkout 对应的 PM 测试解释器，没有安装或更新依赖。

```sh
export HERMES_RUNTIME_DIR="$HOME/.cache/hermes-agent-dev/tools"
export HERMES_HOME="$HOME/.cache/hermes-agent-dev/home"
source ./activate
scripts/run_tests.sh -j 2 hermes-plugins/ --file-retries 0
```

直接使用 runner 还遇到固定的磁盘临时目录不可写。为继续做授权范围内的验证，使用临时 runner 副本：源码根路径固定为本 checkout；移除真实 home 的 `pytest_live_guard.py` 探测/自动加载；保留显式隔离 HERMES_HOME；把 runner 的临时根迁到沙箱允许的目录。其余 pytest 子进程、环境清理、文件隔离、重试及超时机制保持不变。没有修改仓库的 runner，没有改用裸 pytest。

实际运行前缀如下（临时副本位置仅为本次审计证据）：

<!-- no-tmp: ok — exact temporary audit runner used in this isolated validation -->
`HERMES_HOME=/Users/birderyu/.cache/hermes-agent-dev/home HERMES_PYTHON=/Users/birderyu/.cache/hermes-agent-dev/home/installs/8593ddf563ca66c6/test-environment/gen-b0eccc3fb8434ba1a5cf84cc80008f64/venv/bin/python bash /tmp/ollo-session-tests.sh`

后接的命令参数与结果：

| 参数/检查 | 结果 |
| --- | --- |
| `-j 2 hermes-plugins/ --file-retries 0`（设计复核后全量复测） | **25 文件，525 passed，0 failed，14 skipped**，74.8 秒 |
| `-j 2 hermes-plugins/ollo-bridge/test_identity_availability.py hermes-plugins/ollo-bridge/test_session_authority.py hermes-plugins/ollo-bridge/test_runtime.py --file-retries 0`（设计复核后定向复测） | **32 passed，0 failed，11 skipped**，15.0 秒；新增故障回归 13 过，canonical 11 过，legacy 8 过 |
| 旧 `test_runtime.py` | **8 passed**，保留所有 legacy 断言 |
| 下列 14 个核心文件（初轮，本轮未重复 socket 失败） | **54 passed，64 failed**。全部失败均在本地监听 socket 的 PermissionError；不能算通过，必须沙箱外重跑 |
| `python scripts/check --base HEAD`（初轮，同一 PM 测试解释器） | 12 检查中 10 通过；health 0 blocking/0 advisory；no-tmp 和 ruff 失败见下 |
| `python scripts/check --base HEAD --only health`（最终复核） | **通过，0 blocking / 0 advisory** |
| 同环境 pinned ruff，仅检查本次 5 个 Python 文件（含新增故障测试） | **All checks passed** |
| `git diff --check` | 通过 |

14 项跳过中，11 项是新增 TestClient/TestServer 版本在 `start_server()` 被沙箱拒绝；3 项来自旧位置 socket 测试。router 版本走真实 aiohttp Request/Response/路由/中间件、真实 SQLite admission、真实 TurnRunner 和真实模型工具分派，只将网络传输替换为进程内请求；它不证明真实 HTTP 监听可用。AppleBackend 的写入为替身，未访问 EventKit/iCloud。

新增测试覆盖成功确认、压缩续接反查、同会话下一普通回合拒绝旧 UUID、terminal/报告工具被拦、错误 UUID 拒绝、非 Ollo/其他 Profile/委派/非 API 上下文拒绝、精确 task/turn 匹配、活动中和终态重放、hook 缺失 fail closed、authority 未就绪、取消排队确认、真实 epoch 初始化恢复排队回合但不恢复旧许可。恢复测试使用同一临时数据库和新的 authority epoch/插件请求内存，**不是进程崩溃或实际网关重启验收**。

核心复测命令（沙箱外先按上面的隔离环境激活）：

```sh
scripts/run_tests.sh -j 2 \
  tests/gateway/test_api_authority_runs.py \
  tests/gateway/test_api_authority_execution.py \
  tests/gateway/test_api_authority_retry.py \
  tests/gateway/test_api_authority_store.py \
  tests/gateway/test_api_canonical_projection.py \
  tests/gateway/test_api_server_runs.py \
  tests/gateway/test_session_authority_preadopt_stop.py \
  tests/gateway/test_session_authority_adopted_stop.py \
  tests/gateway/test_session_authority_late_stop.py \
  tests/gateway/test_session_authority_cancel_settlement.py \
  tests/cron/test_canonical_completion_retention.py \
  tests/cron/test_delivery_queue.py \
  tests/cron/test_delivery_retry.py \
  tests/tools/test_safe_worker_tools.py --file-retries 0
```

其中 socket 失败分布：api_authority_runs 1、execution 1、retry 1、store 1、api_canonical_projection 6、api_server_runs 51、safe_worker_tools 3。其余 54 项通过；没有把这些沙箱失败归为插件回归，也没有声称其业务断言已验证。

初轮全仓检查失败都在未改动内容：历史记录 `custom/records/2026-08-30-v0.20.6.md:27` 的临时路径字面量；ruff 的 23 项分布在既有 bridge Apple/采集/合同/脚本/测试及旧 location 测试，主要是 TimeoutError 别名、ISO 时间 Z 替换和可简化表达式。设计复核后本次五个 Python 文件单独重跑 Ruff 全过，health 与 diff 检查也全过；没有重复已知无关的全仓失败或顺手修改范围外历史记录、无关实现。

## 沙箱外复测（设计会话，2026-10-15）

用仓库自己的 runner，在隔离开发环境里跑（`HERMES_RUNTIME_DIR=~/.cache/hermes-agent-dev/tools`、`HERMES_HOME=~/.cache/hermes-agent-dev/home`、`source ./activate`），没有用临时 runner 副本：

| 命令 | 结果 |
| --- | --- |
| `scripts/run_tests.sh -j 2 <上面 14 个核心文件> --file-retries 0` | **14 文件，118 passed，0 failed**（沙箱里的 64 项 socket 失败全部通过） |
| `scripts/run_tests.sh -j 2 hermes-plugins/ --file-retries 0`（含设计复核后的修正） | **25 文件，539 passed，0 failed，0 skipped**（TestClient/TestServer 真实监听版本也通过） |

## 部署后的真实验收

本次仅交付源码和测试。后续部署/重启须由授权维护会话执行。

1. **确认**：Ollo inbox/review 卡片各提交一次；记录本次 UUID、POST run_id、GET task/session 与工具 runtime 对应关系；同会话普通回合、旧 UUID 重放、错误 UUID、其他工具和其他 Profile 均不得取得确认许可。另测压缩续接、并行排队、取消、实际进程重启后的排队/unknown 处置；真实 Apple/iCloud 回执单独核对。
2. **入箱**：capture 创建与 lookup、重复请求、超时 unknown 和原回执恢复，核对真实提醒事项及 capture URL，不把 HTTP 成功当作完成证明。
3. **推送**：报告写入、App/SSE、APNs 到达及相同 execution 去重；失败通知、固定兜底与队列重试不得重复展示或推送。需实际手机确认。
4. **wake**：即时唤醒、同日重复、已有固定 fire、跨日延迟、canonical completion 恢复与报告认领；核对真实 job/execution/queue/网关业务日期，固定 08:00 兜底保持。
5. **位置请求**：授权设备、新鲜位置与缓存、拒绝/超时和 15 秒总预算；其他会话/Profile 不能借用身份，最终以手机权限和真实网关返回为准。

剩余限制：依赖的是 v0.22.1 私有宿主字段和 admission schema，后续升级必须保留这些集成测试。safe worker 不提供插件能力。API 对外监听、真实 Ollo 服务、推送、Apple 写入和进程崩溃恢复尚未在本次环境验收。
