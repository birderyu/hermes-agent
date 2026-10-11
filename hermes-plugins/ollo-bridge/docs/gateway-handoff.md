# C → D 接口核对（2026-10-11，未部署）

依据 Ollo `server/qa/gateway-step2c-2026-10-11.md`、`server/qa/step2-crosscheck-2026-10-11.md`、联合方案开头的对账后设计决定及 5.1–5.5，以及 `contracts/bridge.openapi.yaml`（含 A2 capture lookup、A4）。本次读了网关 `api/bridge.py`、`api/schedule.py`、`api/conversation.py`、`api/security.py`，`core/conversation.py`、`core/schedule.py`、`core/schedule_port.py`，`storage/schedule.py`，以及 `agents/hermes/client.py`、`agents/hermes/schedule.py`。源码核对与替身测试不代表两个服务已联通。

A5 追加依据 `server/qa/step2-crosscheck2-2026-10-11.md`、更新后的两份合同与 `core/errors.py`，修复原 8/9、N1/N2；具体语义及待复测项目见下文。路径、认证与确认身份链不变。

## 确认身份链路

1. App 的 `client_message_id` 是传给网关的 `Idempotency-Key`。网关在 `ScheduleService.prepare` 保存确认，`HermesClient.submit` 原样转发该头给 `POST /v1/runs`，正文仍为原消息。
2. bridge 的 `run_context.py` 在插件注册的 aiohttp middleware 中捕获这个 UUID。必须先通过 Hermes API key 校验；不接收 room grant 作为此能力来源。仅在宿主返回新接受的 202、owner 相符且后台 task 仍活动时，按 run_id 临时登记 `(adapter, owner, UUID)`，任务结束删除；幂等重放不重新授权。宿主 executor 不复制任意 ContextVar，worker 通过线程安全登记查询，不按会话保存“最近 UUID”。v0.21.6 的 handler 创建后台 task 后无 await 即返回，因此 middleware 在其开始执行前完成登记；真实 POST/GET run 测试负责回归该时序。
3. 宿主现有 `_approval_session_key` 提供当前 run_id；插件以 active agent 的 session/task/turn、API 会话续接链、Profile 和请求所有权 scope 交叉核对。本轮 hook 给模型提供可信 `confirmation_id`；工具仍只收一个 ID，但必须与该请求的 UUID 完全相同。模型正文、历史和工具参数都不能替换它。
4. bridge 发 `GET /internal/v1/schedule/confirmations/{confirmation_id}`，头为 `X-Ollo-Session-ID`、`X-Ollo-Task-ID=run_id`、`X-Ollo-Tool-Call-ID`。只对 run 尚未绑定的 `409/operation_in_progress` 做有限只读重查；此前不 claim、不写 Apple。
5. C 用保存的 run_id 回查 Hermes `GET /v1/runs/{run_id}`，使用 **Hermes key**。插件在宿主完成认证/所有权检查后，仅为 Ollo 会话补充 `task_id=run_id` 和 active agent 的实际 session_id。原宿主的 session 级终端 task alias 不变；这个 wire task_id 与 bridge runtime 完全一致。C 仍自行拒绝终态、不匹配 session/task、缺 task 或 task=session。
6. 领取许可后使用网关的 changes/originals；完成回调保留该 attempt/runtime。多个显示组共用 operation_id 时只执行首个许可一次，finish 仍用实际 permit 的 indices，返回模型的显示结果再映射到对应索引。按业务语义校验重复组一致（忽略 `by` 和可选 null），不能把不同修改合成一次。

以上都在插件内完成，未增加 Hermes 核心补丁或改变宿主认证。重启后未执行的旧上下文不恢复为新执行权；幂等重放仍由宿主持久 run 记录回答。

## 已逐项核对的接口

表中“bridge”指独立 bridge Bearer，“Hermes”指 Hermes API Bearer，“owner”指网关 owner Bearer。任何 key 都不因 loopback/Host/来源 IP 获得跨域权限。

| 方向 / 方法与路径 | 认证、字段及核对结果 |
| --- | --- |
| App → 网关 `POST /v1/conversation/messages` | owner；Idempotency-Key=client_message_id；网关先保存确认再转发，收到 run_id 后绑定确认 |
| 网关 → Hermes `POST /v1/runs` | Hermes；`{input,session_id}`，`Idempotency-Key`、`X-Hermes-Session-Key`；原宿主负责准入、压缩续接和持久幂等 |
| 网关 → Hermes `GET /v1/runs/{run_id}` | Hermes + 原宿主 run 所有权；Ollo 响应增加可信 task_id，session 取实际 active agent；非 Ollo 响应不改 |
| bridge → 网关 `POST /internal/v1/reports` | bridge；job_id/execution_id/execution_started_at/business_date/trigger_id、delivery_kind（report/failure）与正文；200/201 回执、suppressed 均按 schema 校验；failure 不作为 agenda 正式报告 |
| bridge → 网关 `GET /internal/v1/reports` | bridge；`before_sequence`、`limit`；已保存报告分页，不重跑 cron |
| bridge → 网关 `GET /internal/v1/reports/{id}` | bridge；报告 UUID；items/interpret 二次核对报告引用也用此接口 |
| bridge → 网关 `GET /internal/v1/context` | bridge；报告/位置资料和身份 hook，不授予 Apple 写权限 |
| bridge → 网关 `POST /internal/v1/location/requests` | bridge；`max_age_seconds`、`timeout_seconds`、宿主身份摘要 `runtime_call_id`；200/202；总等待不超过 15 秒，坐标未来容差为 10 秒（含边界） |
| bridge → 网关 `GET /internal/v1/location/requests/{id}` | bridge；只轮询首次返回 ID，不延长原 expires_at |
| App → 网关 `POST /v1/schedule/wake` | owner；request_id/device_id/local_date/source；网关再构造 operation_id |
| 网关 → bridge `POST /v1/ollo-bridge/schedule/wake` | bridge；`{operation_id,request}`；3 秒预算，宿主调度锁内检查 paused/runnable；`paused_job` 为 503，与 C 的错误映射一致 |
| bridge → 网关 `GET /internal/v1/schedule/agenda/{business_date}` | bridge；只把 generated 当作已有报告；触发意图不阻止固定兜底 |
| App → 网关 `POST /v1/schedule/capture` | owner；request_id/text/created_at/source；网关只有持久 succeeded 才返回 item_id |
| 网关 → bridge `POST /v1/ollo-bridge/schedule/capture` | bridge；`{operation_id,request}`；8 秒预算，helper 上限 6 秒；先 claim，再创建，再 finish |
| 网关 → bridge `POST /v1/ollo-bridge/schedule/capture/lookup` | bridge；A2 请求 `{operation_id,request_id,attempt}`，attempt 是 UUID；4 KiB；HTTP 200 found/absent/unavailable；同 request_id 的活动 helper 返回 unavailable/in_progress；不接受调用方的过期时间或 helper 状态 |
| bridge → 网关 `POST /internal/v1/schedule/operations/{id}/claim` | bridge；`{claim_id,runtime}`；capture runtime 为 `{source:"api",operation_id}`，确认组为 `{source:"tool",session_id,task_id,tool_call_id}`；仅 claimed 可启动 helper |
| bridge → 网关 `POST /internal/v1/schedule/operations/{id}/finish` | bridge；`{attempt_id,runtime,outcome}`；与领取一致；只重传完成回执，不能重跑写入 |
| bridge → 网关 `GET /internal/v1/schedule/confirmations/{id}` | bridge + 上述三个 X-Ollo 身份头；核对返回 confirmation_id 和 run_id；不从“最新确认”猜测 |
| App → 网关 `GET /v1/schedule/items?report_id=…&ids=…` | owner；网关检查报告引用，然后转成 bridge 请求 |
| 网关 → bridge `POST /v1/ollo-bridge/schedule/items` | bridge；`{report_id,ids}`；请求 640 KiB，响应 2097152 字节，6 秒预算、helper 5 秒；完整结果集合，title 上限 4096 UTF-8 字节；响应超限 413/payload_too_large/false，不截断、不修改 originals |
| App → 网关 `POST /v1/schedule/interpret` | owner；`{report_id,item_id,text,current_suggestion?}`；网关获取已保存 GTDItem |
| 网关 → bridge `POST /v1/ollo-bridge/schedule/interpret` | bridge；`{request,item,allowed_item_ids}`；正文 4 MiB，18 秒预算覆盖报告读取、模型与输出校验；全程超时统一 504/interpret_timeout/true；辅助模型禁工具，不写历史/记忆，输出按公共 GTDSuggestion 校验 |
| App/网关 → Hermes `GET /v1/ollo/conversation`、`GET /v1/hermes-plus/conversation` | Hermes；仅返回配置的 `{session_id,name}`；bridge key 不可访问 |

## A4 执行快照与失败投递

`cron/scheduler.py::_save_compose_deliver` 以 `for_failure=not d.success` 调用 `_deliver_result`；崩溃通知也显式传 True。`_live_route_metadata` 目前只有 job/execution/notify 等路由字段，没有这个失败标志。沿用已有幂等 live retry 补丁，进程内合格 Ollo 目标先 `queue.enqueue(execution_id, job, content, for_failure=...)` 再尝试发送，外部 worker 同样先入该队列；INSERT OR IGNORE 保留首次载荷，不被重试或后来的 job 配置改写。

bridge 精确查执行账本的 started_at/scheduled_instant，以及原队列的 job_json/独立 for_failure 列。只有 manual_run_at 与当次 external_trigger 相同且不是固定调度，才输出原 trigger_id 与触发业务日期；次日实际开始仍保留原日期。类型取队列列，不从正文、路由 metadata 或 job 的同名字段推断。缺失快照返回失败，不能降级为当日 report。本轮没有新增或扩大核心补丁。

这里的快照仅在 pending/delivering 期间保留。`get_status` 返回真实当前行，adapter 发送时可读完整载荷；HTTP 成功返回后，宿主 `_finish` 将终态行的 job_json/content 清空，这是上游既有保留策略。完成后的去重依赖结果元数据/墓碑，不再调用 adapter，也不需要恢复快照。第六轮两个测试失败来自在成功 drain 后检查完整快照；已将原逐字相等断言改为对 HTTP 返回前采集的 delivering 行检查，并增加成功清理与不重复发送断言，待复测。

部署需要保持正式和失败投递为单目标 Ollo、无附件、非 relay，以进入现有 opt-in 持久队列。真实临时 wake/due scan/fire claim/队列/live adapter + HTTP 替身测试覆盖跨日、后续 job 变化和原样重试。最新外部结果为插件 215 过、上述 2 项失败，cron 1485 全过；真实网关 failure 落库后不消费 agenda 日唯一键、固定兜底正常保存，仍需双方联调。

## capture 原生核对

创建的 URL 为 `ollo://capture/<request_id>`，每个新 attempt 都使用同一请求标识。标题完整保留合法原文（≤4096 UTF-8 字节），备注只保留创建时间/来源，不嵌入身份标记。items 使用相同 title 上限，不会因捕获文本超过 512 字符而拒绝合法刷新结果。

helper 用完整提醒事项访问权限对全部清单查询，包含已完成事项，按完整 URL 比较；权限前后检查、清单集合变化、读取超时/失败、重复 URL 匹配均不出 absent。只有完整查询无匹配才提供带时区 checked_at。此路径不需要日历权限，也不弹授权提示。

helper 在启动后、读取 stdin 前设置 `alarm(7)`，短于 8 秒执行许可，恢复 SIGALRM 默认终止行为并解屏蔽；不依赖父进程或主线程调度，在阻塞输入/EventKit/commit 时也会自行退出。仅操作员 `--authorize` 豁免，该分支只请求权限。bridge 自身超时/取消也 kill + wait；异步 spawn 晚完成时仍由保留的清理 task 终止并回收。

按设计会话确认的 C2/A2 合同，网关校验绑定与 unknown/current attempt，并仅在许可过期 + 30 秒后调用 lookup；应用结果时再次原子检查，过期本身不能释放许可。bridge 不查询许可、不保存执行记录，只登记本进程活动 request_id，完成回收就移除。同 request_id 活动时返回 HTTP 200 `unavailable{reason:in_progress}`；否则直接扫描，包括 Hermes 重启后空登记。lookup 不调用 claim/finish，不自行重写事项或释放执行权。HTTP handler 已接入；替身和原生看门狗测试已补，运行结果待沙箱外复测。

## A5 错误、UTF-8 与完整原值

`protocol.py` 维护公共/bridge 共享的完整 29 个 code 映射（expired 另保留旧事件的 404）。HTTP 客户端不再用小白名单替换合法错误；状态与 retryable 都按合同 code 决定，忽略上游错误的重试提示。401/unauthorized、410/expired、413/payload_too_large、503/not_configured 均保留永久失败；旧 Error 的未知重试策略省略 retryable。无法解析或未知 code 隐藏为合法的 503/dependency_unavailable/true，不会产生原 4xx 配新 code。interpret 外层覆盖报告读取与预算耗尽，将 read_timeout 统一成 interpret_timeout，永久读取失败仍原样返回。

所有 bridge 生成的 JSON 请求/响应、工具文本和 helper 管道使用紧凑 UTF-8，不把中文/emoji 转成 ASCII 转义；必要的引号、反斜杠和控制字符转义计入实际正文预算。items helper 只输出公开字段，Python 同时检查 helper 输出及最终 HTTP 正文的 2097152 字节上限。超限明确 413/payload_too_large/false，由网关/App 缩批重取；200 始终保留全部请求 ID，不通过截断标题或丢项目凑预算。report POST 仍限 262144 字节、body 仍限 128000 字节，计量使用与真实发出相同的 serializer。

A5 离线 schema 已重新导出，GTD 标题类字段与 originals 均接受 4096 UTF-8 字节。helper 的 collect 原值投影、items 公开字段投影与确认比较保留完整标题，不省略或改写 originals。补充行为测试覆盖第二份对账报告的四类复现，另含非法 code/status 组合、预算各阶段耗尽、2 MiB 精确边界与控制字符超限、513 字符/4096 字节多字节标题，以及长原值进入确认 helper。静态检查完成后，设计会话在沙箱外回传 A5 插件 286 passed / 0 failed、cron 1485 passed / 11 skipped（上游既有跳过）；无宿主补丁增量，真实服务联合验收仍待完成。

第三轮 N3 已修插件的字段字节错误分类：`iter_errors()` 检查所有失败及其 context，x-max-utf8-bytes（含组合 schema 内层）返回 413/payload_too_large/false，其他 schema 错误仍 400；合法替代分支不继承失败分支的字节限制。interpret 模型输出校验仍转为 invalid_model_output。新增 HTTP/真实 jsonschema/模型替身用例尚待沙箱外复测，不能用上面的 A5 结果代替；第三轮 N4 为网关转发问题，本仓库未改。
