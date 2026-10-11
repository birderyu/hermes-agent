# 补丁清单

`custom` 相对官方基线 `main` 的全部改动。每次变基后更新提交号；补丁移除时移到「已移除」，不要直接删掉。

基线：官方 `v0.21.6`（`818c13be1d`）。`git log --oneline main..custom` 应与下表一致；已在 `custom` 上撤回、等下次变基再丢掉的提交列在「已移除」里。

## 现行补丁

| 提交 | 内容 | 为什么还需要 | 上游状态 | 什么时候可以移除 |
| --- | --- | --- | --- | --- |
| `3a1874a17d` fix(cron): pass execution_id to live-adapter delivery metadata | 定时任务走 live adapter 投递时，把执行 ID 放进元数据 | `ollo-inbox` 按执行 ID 给定时报告去重，没有就拒收 | 无（一行，适合提 PR） | 上游合入；或 Ollo 网关接管收件箱后不再需要（Ollo 网关第 2 步） |
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

## 运行环境里的非代码调整

### 更新器设置（2026-10-11 起）

- 运行目录只保留 `origin` 远端，没有 `upstream`：避免更新器把 `main` 同步到官方开发主线并推到 fork。
- `~/.hermes/config.yaml` 里 `updates.auto_switch_parked_branch: false`：避免更新器把检出从 `custom` 切走。

原因见 [v0.21.6 升级记录](records/2026-10-11-v0.21.6.md#部署)。

### 会话自动重置：不启用

v0.21.6 起，按闲置和每日定时自动重置会话要装插件 `hermes-session-reset-policy`，否则只在 `/new`、`/reset` 时重置。这个机制只作用于 iMessage 这类消息渠道，Ollo 走的 API 会话本来就不受它管。2026-10-11 起不再使用 iMessage，决定不装。`config.yaml` 里的 `session_reset` 块还在，网关启动和 `hermes doctor` 会提示它不再生效；删掉这个块就不再提示。

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
