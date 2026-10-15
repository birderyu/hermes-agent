# 补丁清单

`custom` 相对官方基线 `main` 的全部改动。每次变基后更新提交号；补丁移除时移到「已移除」，不要直接删掉。

基线：官方 `v0.22.1`（`297552a9d6`，2026-10-15 升级）。`git log --oneline main..custom` 应与下表一致。

## 现行补丁

| 提交 | 内容 | 为什么还需要 | 上游状态 | 什么时候可以移除 |
| --- | --- | --- | --- | --- |
| `1abf25911b` fix(cron): pass execution_id to live-adapter delivery metadata | 定时任务走 live adapter 投递时，把执行 ID 放进路由元数据（v0.22.1 起在 `cron/scheduler_delivery.py` 的 `_live_route_metadata` 普通分支） | ollo-bridge 据此查宿主执行账本，并让 Ollo 网关按执行 ID 幂等 | v0.22.1 仍没有：execution_id 只用于 Bot Chat 身份和外部 worker 判断（一行，适合提 PR） | 上游等价透传，或 ollo-bridge 下线 |
| `71cfa6f80a` fix(cron): idempotent live delivery retry for single-target adapters | 声明 `idempotent_delivery=True` 的 live adapter 投递失败、发送进程死亡或超时后，按同一执行有界重放 | Ollo 的报告投递靠它做到「至少一次且不重复」 | v0.22.1 只部分覆盖，见下 | 见下 |
| `402c399d50` `33ef9241fb` `c2f90a1bb8` `cb739533b7` `ca29601010` | `hermes-plugins/` 目录与忽略规则；`ollo-inbox`、`ollo-location`、`ollo-conversation` 及其文档、测试 | Ollo 服务端插件源码在本 fork 维护 | 个人插件，不提交上游 | 插件全部下线 |
| `384c80420a` `cd3873d8ad` `321f745aab` `b00f191658` `ede9e955c6` | `ollo-bridge`（报告投递与读取、位置、身份、日程 handler、受限确认、GTD 正式切换工具、EventKit 诊断）与 `ollo-conversation` 语气开关 | 运行环境唯一在用的 Ollo 插件 | 同上 | 同上 |
| `9dd4cb0b4d` `16f38f8393` `1ab6119a2a` `40ec1a5ff4` `a76cf21de0` `8663d9e22e` `2855a1fafc` `603a2c4b15` `2043ff7e70` `929779e08b` `d0ca63343c` `d9a892d1f6` 及本表所在提交 | `custom/` 维护文档与修订记录（2026-10-11 由 `fork/` 改名） | 本 fork 的修订方式与记录 | — | — |

### Ollo 插件的去留

| 插件 | 现状 | 计划 |
| --- | --- | --- |
| `ollo-bridge` | 运行环境唯一在用的 Ollo 插件；2026-10-14 起 4 个 GTD 任务投递 `ollo:main` | 随 Ollo 网关计划演进 |
| `ollo-conversation`、`ollo-inbox`、`ollo-location` | 10-11 起在运行环境停用，10-15 运行环境副本移入备份；仓库源码仍在 | 确认不再需要回滚后从仓库删除，由 Ollo 设计会话决定 |

各插件的设计、对账与部署经过见 [记录](records/README.md)（`2026-10-11-ollo-*`、`2026-10-14-ollo-gtd-production-switch`、`2026-10-15-*`）和 [ollo-bridge README](../hermes-plugins/ollo-bridge/README.md)。

### 投递重试补丁（`71cfa6f80a`）

**为什么需要：** 原 `cron/delivery_queue.py::_finish` 将错误置为终态 `failed`；`claim_next` 只选 `pending`，因此 adapter 返回失败后不会再发送。`recover_abandoned` 将死进程已领取发送置为 `unknown`，同样不重试。这是原 at-most-once 策略，不是“持久队列自然会重试”。此外 `cron/scheduler_delivery.py::_deliver_result` 原来只为 detached worker 入队，`cron/scheduler.py::_launch_external_cron_worker` 在非 systemd 拓扑（包括当前 macOS 部署方式）走进程内路径，单改队列不覆盖这个入口。

**最小范围：** 新增 `cron/delivery_retry.py` 承载 opt-in 策略；`cron/delivery_queue.py` 加三列和领取/失败/恢复挂点；`cron/scheduler_delivery.py` 只加 12 行挂点，不搬旧代码。仅真实 live adapter 显式 `idempotent_delivery=True`、单目标、无附件、非 relay 且有 execution_id 时认证。进程内发送先入原队列，失败保留同 execution/job/content，最多 5 次，退避 1/5/30/120 秒；已认证发送的在途超时不报成功、不走 standalone。未认证平台仍失败即终结、未知即不重试；序列化任务字段不能自行获得重试资格。

**A4 对账（未扩大核心补丁）：** 上述进程内先入队的挂点已使 bridge 能读到与外部 worker 相同的不可变 job_json 和独立 for_failure 列。插件以原 execution_id 查账本和该队列行，按当次 manual_run_at 匹配 wake，保留原 trigger_id/业务日期（含次日才开始）；delivery_kind 由队列列转成 failure/report，不信任正文或 metadata。缺队列快照拒绝投递，不补造当日正式报告。无需新增 metadata 字段、调度快照文件或修改现有三处核心文件；真实任务仍需满足单目标/无附件/非 relay，并核对 failure_deliver。

**快照生命周期：** pending/delivering 保留完整载荷供发送与重试；adapter 成功返回后 `_finish` 调用 `_prune_terminal_unlocked` 清空终态 job_json/content，仅保留结果元数据和幂等墓碑。这是上游既有策略（源自 `29e5172487`），`get_status` 不会隐藏活动行快照。第六轮插件测试误在成功 drain 后要求完整快照，已修正为在 HTTP 返回 201 前采集真实行，保留逐字相等断言，并补成功清理/终态不再发送断言；生产路径和核心补丁未改。

**上游状态（2026-10-15 对 v0.22.1 的核对）：** 只部分覆盖，不等价。普通 agent cron 已在发送前按执行 ID 持久入队，队列保留不可变快照（`cron/delivery_queue.py` 的 `enqueue`）；但 adapter 返回失败后仍置 `failed` 不再重试，已领取的行在发送进程死亡后仍置 `unknown` 不重放，已开始的发送超时仍按「假定成功」处理，也没有 opt-in 的认证、次数与退避。上游新增的终态回写（`recover_abandoned` 里的 `settle_quietly`、`cron/delivery_outcome.py` 对账）在变基中保留，认证重试只计入恢复数。逐项证据见 [v0.22.1 升级记录](records/2026-10-15-v0.22.1.md)。

**什么时候可以移除：** 上游提供等价的幂等 adapter 声明、进程内持久入队、失败/死进程重放、超时不假成功能力时，重接插件后整体移除；或所有声明此能力的消费者下线。移除/回滚前先处理原队列里的 pending/in-flight 行，不能静默丢弃或无条件重放。

**行为测试：** `tests/cron/test_delivery_retry.py` 覆盖相同载荷重放、退避上限、死进程恢复、非认证失败/unknown、任务伪造开关、fan-out/附件/relay 排除、发送超时与 standalone 差异。`hermes-plugins/ollo-bridge/test_cron.py` 以真实宿主 router/queue 和网关 HTTP 替身验证进程内失败后重试。原 `tests/cron/test_delivery_queue.py`、`test_cron_relay_dm_origin_user_id.py` 保持原样，供非 opt-in 回归。v0.22.1 上 `tests/cron/` 与 `hermes-plugins/` 全部通过。

## 运行环境里的非代码调整

### 更新器设置（2026-10-11 起）

- 运行目录只保留 `origin` 远端，没有 `upstream`：避免更新器把 `main` 同步到官方开发主线并推到 fork。
- `~/.hermes/config.yaml` 里 `updates.auto_switch_parked_branch: false`：避免更新器把检出从 `custom` 切走。

原因见 [v0.21.6 升级记录](records/2026-10-11-v0.21.6.md#部署)。

### 会话自动重置：不启用

v0.21.6 起，按闲置和每日定时自动重置会话要装插件 `hermes-session-reset-policy`，否则只在 `/new`、`/reset` 时重置。这个机制只作用于 iMessage 这类消息渠道，Ollo 走的 API 会话本来就不受它管。2026-10-11 起不再使用 iMessage，决定不装。同日从 `~/.hermes/config.yaml` 删掉了 `session_reset` 块（`mode: both`、`idle_minutes: 1440`、`at_hour: 4`），网关启动不再提示；删除前的配置备份在 `~/.hermes/backups/cleanup-20261011/config.yaml.pre-session-reset`。

### 时区：`timezone: Asia/Shanghai`（2026-10-12 起）

`~/.hermes/config.yaml` 里的 `timezone: Asia/Shanghai` 是 ollo-bridge 依赖的配置。bridge 的 wake 要求 `cron.get_timezone()` 明确是 Asia/Shanghai；没有这一行时它返回 None（跟随本机时区），Ollo 里点「我醒了」一律返回 `503/not_configured`。2026-10-12 首次真机测试因此失败，加上这一行并重启网关后修复。本机原本就是 +08:00，加入前后所有任务的 `next_run_at` 逐条一致，实际时间不变；这个值也会作为 `TZ` 传给工具子进程。加入前的配置备份在 `~/.hermes/backups/pre-timezone-202610121338/config.yaml`。

ollo-bridge 下线，或者 bridge 不再检查时区之后，才可以删掉。

### 会话自动清理：关闭（2026-10-15 起）

v0.21.6 起 `sessions.auto_prune` 默认开启，网关会删除已结束且 90 天未活动的会话（置顶和进行中的不删）。用户要全部保留，`~/.hermes/config.yaml` 加了 `sessions.auto_prune: false`。当时最早的会话是 07-18，还没有被删过的。修改前的配置备份在 `~/.hermes/backups/pre-v0.22.1-20261015/config.yaml.pre-auto-prune`。只改了主档案，其他档案（care、gold、wiki 等）仍是默认。

## 已移除

| 原提交（最后的 SHA） | 内容 | 移除日期 | 原因 |
| --- | --- | --- | --- |
| `b0958faafa` fix(photon): accept empty iMessage poll titles；`2f360fa07c` fix(photon): scope native poll lifecycle to clarify；`db496d81b7` fix(photon): align voice attachment filename with m4a | 空投票标题的 Spectrum 补丁；`clarify` 原生投票的生命周期与多选；语音附件 `.m4a` 文件名 | 2026-10-11 | 不再使用 iMessage，三个补丁都满足退出条件。先在 `custom` 上由撤回提交 `e596821fad` 抵消，2026-10-15 升级到 v0.22.1 的变基中原提交与撤回提交一起丢掉。同日运行环境关闭了 Photon。重写后的投票生命周期始终没有真机验证 |
| （运行环境）语言覆盖 `custom/runtime/locales/zh.yaml`、`en.yaml` | 改写 `gateway.session.auto_reset_notice` 的文案 | 2026-10-11 | 不装会话自动重置插件，这条提示不会再出现。仓库里的文件删除，运行环境的副本移到 `~/.hermes/backups/cleanup-20261011/locales/` |
| `866b894af1` fix(i18n): localize gateway session reset notices | 重置提示与会话信息块本地化 | 2026-10-11 | 上游已本地化（`gateway.session.*`）；剩下的措辞问题改用运行时语言覆盖。原补丁改 `run_turn.py` 和 17 个语言文件，每次升级都冲突 |
| `bed1ad3fea` fix(photon): decode iMessage reply messages | 展开 `reply` 内容 | 2026-10-11 | 上游 `348a68bb4e`（PR #100663）等价且更完整 |
| `98d2dbc03d` fix(photon): drop receipt loops and repeat votes | 丢弃 `read` 回执 | 2026-10-11 | 上游已丢弃 `read`/`read_receipt`；「首次投票锁定」早被生命周期补丁取代 |
| `fd41a27978` `b4cd89cbdf` `ab63c7103f` `5be1fe6b48` `7e2df9d1b1` `183f2d8f5d` `5b695b0887` `37f76909b8` `5815802d48` | 位置卡片与 Find My 定位一组；`spectrum-ts` 12.8.0；mini-app 入站补丁 | 2026-10-11 | Photon Cloud 不返回 Find My 位置数据（[上游 #53](https://github.com/photon-hq/advanced-imessage-ts/issues/53) 未修），这组只提供兜底提示；位置改由 Ollo App 提供；它靠改写 Spectrum 依赖代码，维护成本高。sidecar 回到上游 `spectrum-ts 12.7.0` |
| `693c0e1c62`（官方提交） | 删除大小写冲突的 contributor 文件 | 2026-09-11 | 新标签已不含冲突 |

各补丁的来龙去脉见 [记录](records/README.md)。
