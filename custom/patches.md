# 补丁清单

`custom` 相对官方基线 `main` 的全部改动。每次变基后更新提交号；补丁移除时移到「已移除」，不要直接删掉。

基线：官方 `v0.21.6`（`818c13be1d`）。`git log --oneline main..custom` 应与下表一致；已在 `custom` 上撤回、等下次变基再丢掉的提交列在「已移除」里。

## 现行补丁

| 提交 | 内容 | 为什么还需要 | 上游状态 | 什么时候可以移除 |
| --- | --- | --- | --- | --- |
| `3a1874a17d` fix(cron): pass execution_id to live-adapter delivery metadata | 定时任务走 live adapter 投递时，把执行 ID 放进元数据 | `ollo-inbox` 与候选 `ollo-bridge` 均需要准确执行身份；bridge 据此查宿主账本并让网关幂等 | 无（一行，适合提 PR） | 上游等价透传；或所有消费者下线。网关接管收件箱后 bridge 仍需要，不能直接移除 |
| `0dfb4385d6` chore(plugins): move Hermes+ server plugins into custom fork | `hermes-plugins/` 目录与忽略规则 | Ollo 服务端插件源码在本 fork 维护 | 个人插件，不提交上游 | 插件全部下线 |
| `237f274a16` feat(plugins): rename Hermes+ plugins to Ollo and add ollo-conversation | `ollo-inbox`、`ollo-location`、`ollo-conversation` | Ollo App 与 Ollo 网关在用，见下 | 同上 | 按 Ollo 网关计划逐个下线 |
| `08f696a1ba` docs(plugins): 更新 Ollo 部署与验证记录 | 插件 README | — | — | 随插件 |
| `7d7752503f` chore(plugins): drop stale hermes-plus gitignore exceptions | 删掉根 `.gitignore` 里只针对旧插件名的例外 | 改名后由 `hermes-plugins/.gitignore` 负责 | — | — |
| `cce920898c` test(plugins): follow v0.21.6 run stream in ollo-conversation mood test | `ollo-conversation` 测试改用 v0.21.6 的 `_RunStream` | 上游把任务事件队列换成了带序号和回放的流 | — | 随插件 |
| （本表所在提交）docs(fork)、docs(custom): 维护文档 | `custom/` 目录（2026-10-11 由 `fork/` 改名） | 本 fork 的修订方式与记录 | — | — |

### Ollo 插件的去留

Ollo 网关（Ollo 仓库的 `server/`）在 2026-10-10 上线，App 改为只连网关。插件随网关的分步计划下线：

| 插件 | 现在的用途 | 计划 |
| --- | --- | --- |
| `ollo-conversation` | 自称 Ollo；回复末尾的 `ollo-mood` 语气标记（网关从中取语气）；`GET /v1/ollo/conversation`（网关配置时发现会话） | 网关第 3 步自己分类语气后，只留「自称 Ollo」，并入薄插件 `ollo-bridge` |
| `ollo-inbox` | `ollo:main` 投递平台、收件箱接口、`get_gtd_reports` 工具 | 网关第 2 步接管收件箱后下线；App 已不直接访问它的接口 |
| `ollo-location` | `get_user_location` 工具、位置接口与唤醒推送 | 网关第 2 步接管位置后，工具并入 `ollo-bridge`；App 已不直接访问它的接口 |

2026-10-11 未提交候选：`ollo-conversation` 已并入 `mood:false` 开关，关闭辅助分类并清除顶层语气围栏；保留 `cce920898c` 的 v0.21.6 流测试适配。设计会话已核验 D1 与线上逐字一致并确认通过；本会话未访问线上，见 [归并记录](records/2026-10-11-ollo-mood-toggle.md)。

2026-10-11 D2 未提交候选：新增唯一 `ollo-bridge`，承接报告投递/读取、位置工具、身份、四个日程 handler 与受限确认；旧三个目录保留，部署时再停用。设计复审后只保留下述一项通用核心补丁。执行时间/wake 身份改由插件读取既有执行账本与原投递队列快照；API run 身份复用宿主已有 approval ContextVar 和 active agent 映射，无需新增核心表面。Telegram 路由搬移及其测试引用改动已撤回。详情见 [源码候选记录](records/2026-10-11-ollo-bridge.md) 与 [插件 README](../hermes-plugins/ollo-bridge/README.md)。

同日 A5 对账复查修复仅在插件内：interpret 全程超时归一、完整合同错误映射、紧凑 UTF-8 与 items 2097152 字节响应上限、GTD 标题/originals 4096 UTF-8 字节。设计会话沙箱外回传插件 286 passed / 0 failed、cron 1485 passed / 11 既有 skipped。第三轮 N3 补齐字段字节超限的 413 分类及组合 schema 测试，待再次复测；未新增或扩大宿主补丁，详见上述记录。

同日 D12 在插件内补三个固定只读 cron 入口、六清单/三日历默认范围、500 项失败上限及 `apple.lists/calendars` 一一对应表。读写目标与 originals 比较共用逻辑名；对照设计会话提供的原任务/Skill 快照，产出四段完整提示词与 Skill 差异。Python AST/JSON/引用/差异检查和 Swift typecheck 通过，行为测试待沙箱外执行。没有新增或扩大宿主补丁：核验到普通 agent cron 的 Script Error 不自动设置 for_failure，脚本也收不到原 wake 日期；失败日账和跨日采集边界明确留在部署验收要求中，不靠提示词假装解决，见上述记录与插件 README。

同日 D13 仅修文档：报告建议走卡片确认，用户直接请求保留对话明确确认后的 Apple Skill 执行。按用户提供的当前配置写精确 enabled 差异，不要求新增 bridge entry/timezone，不新增或替换 Ollo/API 工具白名单；没有任何代码或宿主补丁增量。

### 未提交：幂等 live adapter 的有界投递重试

**为什么需要：** 原 `cron/delivery_queue.py::_finish` 将错误置为终态 `failed`；`claim_next` 只选 `pending`，因此 adapter 返回失败后不会再发送。`recover_abandoned` 将死进程已领取发送置为 `unknown`，同样不重试。这是原 at-most-once 策略，不是“持久队列自然会重试”。此外 `cron/scheduler_delivery.py::_deliver_result` 原来只为 detached worker 入队，`cron/scheduler.py::_launch_external_cron_worker` 在非 systemd 拓扑（包括当前 macOS 部署方式）走进程内路径，单改队列不覆盖这个入口。

**最小范围：** 新增 `cron/delivery_retry.py` 承载 opt-in 策略；`cron/delivery_queue.py` 加三列和领取/失败/恢复挂点；`cron/scheduler_delivery.py` 只加 12 行挂点，不搬旧代码。仅真实 live adapter 显式 `idempotent_delivery=True`、单目标、无附件、非 relay 且有 execution_id 时认证。进程内发送先入原队列，失败保留同 execution/job/content，最多 5 次，退避 1/5/30/120 秒；已认证发送的在途超时不报成功、不走 standalone。未认证平台仍失败即终结、未知即不重试；序列化任务字段不能自行获得重试资格。

**A4 对账（未扩大核心补丁）：** 上述进程内先入队的挂点已使 bridge 能读到与外部 worker 相同的不可变 job_json 和独立 for_failure 列。插件以原 execution_id 查账本和该队列行，按当次 manual_run_at 匹配 wake，保留原 trigger_id/业务日期（含次日才开始）；delivery_kind 由队列列转成 failure/report，不信任正文或 metadata。缺队列快照拒绝投递，不补造当日正式报告。无需新增 metadata 字段、调度快照文件或修改现有三处核心文件；真实任务仍需满足单目标/无附件/非 relay，并核对 failure_deliver。

**快照生命周期：** pending/delivering 保留完整载荷供发送与重试；adapter 成功返回后 `_finish` 调用 `_prune_terminal_unlocked` 清空终态 job_json/content，仅保留结果元数据和幂等墓碑。这是上游既有策略（源自 `29e5172487`），`get_status` 不会隐藏活动行快照。第六轮插件测试误在成功 drain 后要求完整快照，已修正为在 HTTP 返回 201 前采集真实行，保留逐字相等断言，并补成功清理/终态不再发送断言；生产路径和核心补丁未改。

**上游状态：** v0.21.6 及本地 `upstream/main` 快照 `a62979dc3601` 的队列均缺少这一 opt-in 能力。原实现最近相关提交 `eb2781dd2f` 解决及时 drain，未加入失败重试。本轮没有 fetch、查询线上 issue 或提交 PR，不据此声称上游最新状态。

**什么时候可以移除：** 上游提供等价的幂等 adapter 声明、进程内持久入队、失败/死进程重放、超时不假成功能力时，重接插件后整体移除；或所有声明此能力的消费者下线。移除/回滚前先处理原队列里的 pending/in-flight 行，不能静默丢弃或无条件重放。

**行为测试：** `tests/cron/test_delivery_retry.py` 覆盖相同载荷重放、退避上限、死进程恢复、非认证失败/unknown、任务伪造开关、fan-out/附件/relay 排除、发送超时与 standalone 差异。`hermes-plugins/ollo-bridge/test_cron.py` 以真实宿主 router/queue 和网关 HTTP 替身验证进程内失败后重试；A4 增加真实临时 wake/due scan/fire claim 的跨日开始、后续 job 变化、报告/失败类型和固定兜底。原 `tests/cron/test_delivery_queue.py`、`test_cron_relay_dm_origin_user_id.py` 保持原样，供非 opt-in 回归。设计会话第六轮回传 cron 1485 项全过；插件上述两个参数的测试时序修复待沙箱外复测，本会话未跑测试。

## 运行环境里的非代码调整

### 更新器设置（2026-10-11 起）

- 运行目录只保留 `origin` 远端，没有 `upstream`：避免更新器把 `main` 同步到官方开发主线并推到 fork。
- `~/.hermes/config.yaml` 里 `updates.auto_switch_parked_branch: false`：避免更新器把检出从 `custom` 切走。

原因见 [v0.21.6 升级记录](records/2026-10-11-v0.21.6.md#部署)。

### 会话自动重置：不启用

v0.21.6 起，按闲置和每日定时自动重置会话要装插件 `hermes-session-reset-policy`，否则只在 `/new`、`/reset` 时重置。这个机制只作用于 iMessage 这类消息渠道，Ollo 走的 API 会话本来就不受它管。2026-10-11 起不再使用 iMessage，决定不装。同日从 `~/.hermes/config.yaml` 删掉了 `session_reset` 块（`mode: both`、`idle_minutes: 1440`、`at_hour: 4`），网关启动不再提示；删除前的配置备份在 `~/.hermes/backups/cleanup-20261011/config.yaml.pre-session-reset`。

## 已移除

| 原提交（最后的 SHA） | 内容 | 移除日期 | 原因 |
| --- | --- | --- | --- |
| `b0958faafa` fix(photon): accept empty iMessage poll titles；`2f360fa07c` fix(photon): scope native poll lifecycle to clarify；`db496d81b7` fix(photon): align voice attachment filename with m4a | 空投票标题的 Spectrum 补丁；`clarify` 原生投票的生命周期与多选；语音附件 `.m4a` 文件名 | 2026-10-11 | 不再使用 iMessage，三个补丁都满足退出条件。在 `custom` 上由 `revert(photon): drop iMessage patches` 撤回，Photon 目录与官方 v0.21.6 逐字节一致；下次变基时原提交和撤回提交一起丢掉。重写后的投票生命周期始终没有真机验证 |
| （运行环境）语言覆盖 `custom/runtime/locales/zh.yaml`、`en.yaml` | 改写 `gateway.session.auto_reset_notice` 的文案 | 2026-10-11 | 不装会话自动重置插件，这条提示不会再出现。仓库里的文件删除，运行环境的副本移到 `~/.hermes/backups/cleanup-20261011/locales/` |
| `866b894af1` fix(i18n): localize gateway session reset notices | 重置提示与会话信息块本地化 | 2026-10-11 | 上游已本地化（`gateway.session.*`）；剩下的措辞问题改用运行时语言覆盖。原补丁改 `run_turn.py` 和 17 个语言文件，每次升级都冲突 |
| `bed1ad3fea` fix(photon): decode iMessage reply messages | 展开 `reply` 内容 | 2026-10-11 | 上游 `348a68bb4e`（PR #100663）等价且更完整 |
| `98d2dbc03d` fix(photon): drop receipt loops and repeat votes | 丢弃 `read` 回执 | 2026-10-11 | 上游已丢弃 `read`/`read_receipt`；「首次投票锁定」早被生命周期补丁取代 |
| `fd41a27978` `b4cd89cbdf` `ab63c7103f` `5be1fe6b48` `7e2df9d1b1` `183f2d8f5d` `5b695b0887` `37f76909b8` `5815802d48` | 位置卡片与 Find My 定位一组；`spectrum-ts` 12.8.0；mini-app 入站补丁 | 2026-10-11 | Photon Cloud 不返回 Find My 位置数据（[上游 #53](https://github.com/photon-hq/advanced-imessage-ts/issues/53) 未修），这组只提供兜底提示；位置改由 Ollo App 提供；它靠改写 Spectrum 依赖代码，维护成本高。sidecar 回到上游 `spectrum-ts 12.7.0` |
| `693c0e1c62`（官方提交） | 删除大小写冲突的 contributor 文件 | 2026-09-11 | 新标签已不含冲突 |

各补丁的来龙去脉见 [记录](records/README.md)。
