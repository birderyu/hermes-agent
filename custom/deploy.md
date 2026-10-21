# 部署到 Mac mini

把已推送到 `origin/custom` 的版本部署到运行目录。改代码、测试、推送见 [维护方式](maintenance.md)。部署会重启 Hermes 网关，iMessage 和 Ollo 都会短暂断开；Ollo 网关（`com.birderyu.ollo.gateway`，127.0.0.1:8765）经本机回环连 Hermes（127.0.0.1:8642），Hermes 重启后自动恢复。

## 运行环境的布局（v0.21.6 起）

| 内容 | 位置 |
| --- | --- |
| 代码 | `~/.hermes/hermes-agent`：`custom` 的 git checkout，只部署不开发 |
| Python、node 等工具 | `~/.hermes/tools`（pm 管理） |
| 依赖环境 | `~/.hermes/installs/<哈希>/`（pm 管理的「代」，每次依赖变化生成新的一代） |
| 命令入口 | `~/.local/bin/hermes` → `~/.hermes/hermes-agent/.hermes/bin/hermes`（都由更新流程生成） |
| 常驻服务 | `~/Library/LaunchAgents/ai.hermes.gateway.plist`，经 `/usr/bin/osascript` 启动网关（`hermes gateway restart` 发现定义过时会重写） |
| 运行数据与配置 | `~/.hermes`（`config.yaml`、`.env`、`state.db`、`cron/`、`plugins/`、`skills/` 等），不在本仓库 |
| Ollo 服务端代码 | `~/.hermes/plugins/ollo-bridge`、`~/.hermes/ollo/`（日历助手与配置）、`~/.hermes/scripts/ollo_collect*.py`、GTD 技能与 4 个任务的提示词。2026-10-20 起源码在 Ollo 仓库的 `hermes/`，不随 `hermes update` 部署 |

v0.21.5 及以前，依赖装在 `~/.hermes/hermes-agent/venv`，LaunchAgent 直接运行其中的 Python；第一次升级到 pm 管理的版本时，更新流程建立新环境、重新生成入口和服务，旧 `venv` 不再使用（2026-10-11 的升级前副本在 `~/.hermes/backups/pre-v0.21.6-20261011/venv`）。

## 运行目录为什么还保留 git

运行目录可以不要 git（官方 Docker、Nix、桌面 App 用 `install-stamp.json` 标识版本），但源码安装这条路上，Hermes 自己的工具都依赖 git：

- 运行版本标识：没有安装戳时，`gateway_state.json` 的 `code_sha`、`hermes --version` 读 git；
- `hermes update` 的拉取、换代、旧版本更新器向新版本交接、中断恢复，都基于 git checkout；
- 回滚就是切回旧提交。

所以保留 git，但只当「部署通道」用：不在这里改文件、不提交、不变基；唯一的写操作是更新流程把它对齐到 `origin/custom`。开发在 `~/Projects/hermes-agent`。

## 部署步骤

前提：要部署的提交已在 `origin/custom`，并在开发副本通过测试（见 [维护方式](maintenance.md#测试与基线对照)）。以下命令里 `D` 是日期，`B` 是本次备份目录。

### 1 预检（只读）

```bash
hermes update --plan --branch custom              # 列出会重启的服务
git -C ~/.hermes/hermes-agent status --short --branch
launchctl print gui/$(id -u)/ai.hermes.gateway | grep -E 'state|program'
hermes cron list
```

- 运行目录除已知的未跟踪文件外必须干净；有改动先弄清楚来源，不要让更新流程把它们 stash 掉。
- 确认没有正在进行的任务；Mac mini 上的 Hermes 桌面 App 要先退出（它的后端也跑在运行环境里，并会干扰日历/提醒事项的权限归属）。
- 记下 Ollo 网关的验证结果作对照（Ollo 仓库 `server/deploy/install.py verify`）。
- `~/.hermes/plugins/` 里还有不归本 fork 管的用户插件（例如 `bot-handoff`、`orca-status`）：确认它们用到的钩子名（`hermes_cli/plugins.py` 的 `VALID_HOOKS`）、导入的模块和调用的 `hermes` 命令行参数在新版本里还在，升级后看日志有没有加载失败。

### 2 备份

```bash
D=$(date +%Y%m%d); B=~/.hermes/backups/pre-<版本>-$D; mkdir -p "$B"
git -C ~/.hermes/hermes-agent branch "backup/custom-pre-<版本>-$D" HEAD
cp -p ~/.hermes/config.yaml ~/.hermes/.env "$B"/                       # 只复制，不打开
cp -p ~/Library/LaunchAgents/ai.hermes.gateway.plist ~/.local/bin/hermes "$B"/
cp -a ~/.hermes/plugins ~/.hermes/cron/jobs.json "$B"/
for db in state kanban projects response_store runs_idempotency shared-state; do
  sqlite3 ~/.hermes/$db.db ".backup '$B/$db.db'"                         # 在线一致性备份
done
```

依赖环境换代（例如从旧 `venv` 迁到 pm）时，另外 `cp -a ~/.hermes/hermes-agent/venv "$B/venv"`，回滚用。备份可能含敏感运行数据，留在 `~/.hermes/backups`，不复制到别处。

### 3 更新

运行目录的两项前提（2026-10-11 已设好，每次部署前确认还在）：

```bash
git -C ~/.hermes/hermes-agent remote            # 只能有 origin，不能有 upstream
hermes config get updates.auto_switch_parked_branch   # 必须是 false
```

- 有 `upstream` 远端时，更新器在目标是 `main` 的情况下会把 `main` 同步到官方开发主线，并 `push origin main` 推到 fork。开发副本才需要 `upstream`。
- `auto_switch_parked_branch` 默认是 true：更新目标是 `main` 而检出停在别的分支时，更新器会自动切到 `main`。关掉后，误跑无参数的 `hermes update` 也不会离开 `custom`。

```bash
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=url.https://github.com/.insteadOf GIT_CONFIG_VALUE_0=git@github.com: \
hermes update --branch custom --yes
```

- 运行目录的 `origin` 是 SSH 地址，本机 22 端口常被关闭；上面用环境变量临时改走 HTTPS，不改保存的远端配置。
- `custom` 变基后与运行目录本地分支分叉，更新流程会先把旧 HEAD 存到 `refs/hermes-update-backups/…`，再对齐到 `origin/custom`。
- 更新流程负责：安装依赖（pm）、重新生成 `~/.local/bin/hermes`、重启网关（必要时重写 LaunchAgent）、重建 TUI、网页界面和桌面 App。一定带 `--branch custom`。
- Photon sidecar 的依赖在 sidecar 启动时按新的 lockfile 重装，`postinstall` 打的是上游自带的 Spectrum 补丁（本 fork 不再改 sidecar）。
- 更新结束后看最后一行和 `hermes pm status` 的 `fleet`：网关的 `code_sha` 必须等于 `origin/custom`。更新器报「not claiming success」或检出不在 `custom` 时，按下面的「更新没对齐时」处理，不要直接重试。

#### 更新没对齐时

从 0.21.5 及更早版本（没有 pm 的版本）升级时，旧更新器拉完代码后把后半程交给新代码，`--branch` 在交接中丢失，新代码按 `main` 继续（2026-10-11 就是这样，见 [记录](records/2026-10-11-v0.21.6.md)）。处理：

```bash
git -C ~/.hermes/hermes-agent checkout custom          # 回到 custom（检出必须干净）
git -C ~/.hermes/hermes-agent branch -f main <官方基线>  # 本地 main 退回接受的基线
hermes update --branch custom --yes                     # 新版更新器补完未完成的步骤
hermes pm install                                       # 已是最新提交时更新器不再同步依赖，这里按当前 lockfile 补一次
hermes gateway restart
```

然后核对 `gateway_state.json` 的 `code_sha`，并检查 fork 上的 `main` 有没有被推走（`git ls-remote origin refs/heads/main`），被推走就从开发副本用 `--force-with-lease` 推回基线。

#### 更新器把变基后的升级当成降级时

报错是 `✗ Refusing to move this install to …: that release predates the gateway runtime these session stores now use. Downgrading is unsupported.`，更新器什么都没动。检查在 `hermes_cli/update_downgrade_guard.py`，三个条件同时成立才拒绝：

1. 目标提交不是运行目录当前 HEAD 的后代。变基后的 `custom` 永远满足这一条。
2. 目标的 `hermes_state*.py` 里没有 `CREATE TABLE IF NOT EXISTS session_admissions`，也就是目标版本没有「网关运行时」的账本表。
3. 某个档案的 `state.db` 里已有 `session_admissions` 或 `worker_executions` 记录。

官方用户沿着标签向前更新时第 1 条不成立，所以不会被拦。2026-10-20 从 v0.22.1（官方错误构建，带了运行时）升 v0.22.2（撤回了运行时）时碰上过，见 [v0.22.2 升级记录](records/2026-10-20-v0.22.2.md)。处理：

- 先判断代价：在不认识账本表的版本上，有账本记录的会话删不掉，也清理不了（`ON DELETE RESTRICT`）。官方升级同样有这个代价（v0.22.2 发行说明的已知问题），要用户确认能接受。不能接受就先留在当前版本，等目标版本带回运行时（之后的发行版），那时第 2 条不成立，自然放行。
- 用户同意后手工对齐，结果等同官方的向前更新：

```bash
hermes gateway stop                                   # 确认 launchd 已卸载、没有进行中的任务
git -C ~/.hermes/hermes-agent status --short          # 必须干净；回退分支已在备份步骤建好
git -C ~/.hermes/hermes-agent reset --hard origin/custom
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=url.https://github.com/.insteadOf GIT_CONFIG_VALUE_0=git@github.com: \
hermes update --branch custom --yes                   # 已是目标提交：只补后续步骤，会自己重启网关
hermes gateway start                                  # 仅当更新器没有把网关拉起来时
```

  依赖锁文件变了的话再补一次 `hermes pm install`。之后照第 4 步验证，并让 Ollo 真发一条消息。
- 更新器在报错里建议的「恢复升级前的 `state.db` 快照」会丢掉之后的所有会话，除非用户明确要求，不走这条路。

只改 `custom/` 文档的提交不需要部署，运行目录的 HEAD 可以落后 `origin/custom` 若干个纯文档提交。

Ollo 的服务端代码不在本仓库，也不随 `hermes update` 部署：在 Ollo 仓库按 `hermes/deploy/README.md` 用 `python3 hermes/deploy/manage.py {verify|backup|sync|rollback}` 部署插件（白名单同步，测试和文档不进 `~/.hermes`），日历助手、采集脚本、技能和任务提示词各有安装步骤。插件的数据库在 `~/.hermes/plugin-data/`，不随插件替换。插件改了要 `hermes gateway restart`，重启后核对 LaunchAgent 仍经 `osascript` 启动。

### 4 验证

| 项目 | 方法 | 期望 |
| --- | --- | --- |
| 代码版本 | `hermes --version`；`git -C ~/.hermes/hermes-agent status --short --branch` | 版本号为新版本，不带 `.dirty`；在 `custom`，与 `origin/custom` 之间只差纯文档提交 |
| 运行进程 | `hermes pm status` 的 `fleet`、`gateway_state.json` 的 `code_sha` | 网关跑的是要部署的提交，不是旧进程或别的分支 |
| 依赖环境 | `hermes pm doctor`；`hermes pm status` | 工具齐全；最近一次同步成功 |
| 服务 | `launchctl print gui/$(id -u)/ai.hermes.gateway`；`ps` 看网关进程 | running；v0.21.6 起 LaunchAgent 经 `/usr/bin/osascript` 启动 `~/.hermes/hermes-agent/.hermes/bin/hermes`，网关进程的解释器在 `~/.hermes/tools/python-*` |
| 平台 | `gateway_state.json`（看各平台的写入者 PID 是否为当前进程）、网关日志的 `photon connected` | Photon、API server、`ollo` connected。sidecar 的 `/healthz`、`/probe` 需要认证，`/probe` 返回 inconclusive 也不能算健康证明 |
| 插件 | `hermes plugins list`、网关日志 | `ollo-*` 已加载，无报错 |
| 定时任务 | `hermes cron list` | 全部 enabled，下次运行时间正常 |
| Ollo | Ollo 仓库 `server/deploy/install.py verify`（本机、公网）。**Hermes 版本升级后必须加 `--send "…"` 真发一条**，只读的 verify 测不到写入路径（2026-10-15 升级后 `/v1/runs` 全部 409，只读核对没发现，见下） | 能力、会话、历史正常；发送时事件到 `run.completed`，回复出现在历史 |
| 日志 | 重启后的 `~/.hermes/logs/gateway.log`、`gateway.error.log`、`errors.log`（按行首时间戳筛选） | 没有新的 ERROR、Traceback |
| 日历与提醒事项 | 下一次 GTD 定时任务的输出，或日志里的 `full access is not granted` | 能读到。读不到时，在 Mac mini 上给 `osascript` 授权（v0.21.6 起网关由它启动）：解锁屏幕后在 Ollo App 里发「今日安排」，弹窗里允许提醒事项、日历选「完全访问」；没有弹窗就到「系统设置 → 隐私与安全性 → 提醒事项 / 日历」打开 `osascript`，列表里没有它时先在终端 `tccutil reset Reminders com.apple.osascript`、`tccutil reset Calendar com.apple.osascript` 再触发一次。授权给 `osascript` 意味着用它运行的其他脚本也能读日历和提醒事项 |

服务状态以 `launchctl`、状态文件、日志和真实消息交叉判断，不只看 `hermes gateway status`。

不要用 `hermes cron run <任务>` 验证定时任务或日历授权。它的帮助文字写的是「Run a job on the next scheduler tick」，实际上会在调用它的进程里当场执行（`source=direct`，输出 `Ran now: …`），并且照常投递报告：

- macOS 的隐私权限算在启动这条命令的程序头上（终端、Claude 桌面 App 等），不算网关的 `osascript`，所以验证不了网关；
- 每跑一次都会按任务的投递设置发一条真实报告。

验证只能走网关：在 Ollo App 里发消息触发，或者等任务按时运行。看任务是不是调度器触发的，查 `cron/jobs.json` 里的 `last_dispatch`，或者看日志里那次运行所在的线程：网关调度的是 `cron-parallel_*`，直接运行的是 `MainThread`。

升级到会话权威（v0.22.x）以后，API 会话的 `sessions.session_key` 必须为空或等于新的路由键。v0.21 会把客户端声明的 `X-Hermes-Session-Key`（Ollo 是 `hermesplus:main`）写进这一列，官方没有迁移，旧会话一绑定就 `admission_conflict`，`POST /v1/runs` 返回 409。处理：在线备份 `state.db` 后，把这条会话的 `session_key` 置空（`UPDATE sessions SET session_key=NULL WHERE id=<会话> AND session_key=<旧键>`），不用重启；下一次请求会重新绑定成路由键。详见 [v0.22.1 升级记录](records/2026-10-15-v0.22.1.md)。

`hermes photon status` 会打印手机号和 Photon 项目标识，只看「已存储/已安装」这类状态，不要把输出贴进记录。

### 5 记录

在 [records/](records/README.md) 写部署结果，更新 [README](README.md) 的「现状」。

## 回滚

1. `hermes gateway stop`。
2. 代码：`git -C ~/.hermes/hermes-agent reset --hard backup/custom-pre-<版本>-<日期>`。
3. 依赖环境：
   - 同一套环境机制内回滚（都是 pm）：`hermes pm install` 按旧 lockfile 重建；
   - 回到 v0.21.5 这类旧 `venv` 布局：把 `$B/venv` 移回 `~/.hermes/hermes-agent/venv`，把 `$B/hermes`、`$B/ai.hermes.gateway.plist` 放回原位。
4. `launchctl bootout gui/$(id -u)/ai.hermes.gateway`，再 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.hermes.gateway.plist`。
5. 数据库只有在新版本做了旧版本读不了的迁移时才用 `$B/*.db` 恢复，恢复会丢掉升级后的新记录；先把当前库另存一份。
6. 回滚后按第 4 步验证，并写记录。
