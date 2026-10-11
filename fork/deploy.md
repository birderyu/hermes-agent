# 部署到 Mac mini

把已推送到 `origin/custom` 的版本部署到运行目录。改代码、测试、推送见 [维护方式](maintenance.md)。部署会重启 Hermes 网关，iMessage 和 Ollo 都会短暂断开；Ollo 网关（`com.birderyu.ollo.gateway`，127.0.0.1:8765）经本机回环连 Hermes（127.0.0.1:8642），Hermes 重启后自动恢复。

## 运行环境的布局（v0.21.6 起）

| 内容 | 位置 |
| --- | --- |
| 代码 | `~/.hermes/hermes-agent`：`custom` 的 git checkout，只部署不开发 |
| Python、node 等工具 | `~/.hermes/tools`（pm 管理） |
| 依赖环境 | `~/.hermes/installs/<哈希>/`（pm 管理的「代」，每次依赖变化生成新的一代） |
| 命令入口 | `~/.local/bin/hermes`（由更新流程重新生成） |
| 常驻服务 | `~/Library/LaunchAgents/ai.hermes.gateway.plist`（`hermes gateway restart` 发现解释器变了会重写） |
| 运行数据与配置 | `~/.hermes`（`config.yaml`、`.env`、`state.db`、`cron/`、`plugins/`、`skills/` 等），不在本仓库 |
| Ollo 插件 | `~/.hermes/plugins/ollo-*`，从 `hermes-plugins/` 复制安装 |
| 语言覆盖 | `~/.hermes/locales/zh.yaml`、`en.yaml`，从 [fork/runtime/locales](runtime/locales) 复制 |

v0.21.5 及以前，依赖装在 `~/.hermes/hermes-agent/venv`；第一次升级到 pm 管理的版本时，更新流程会建立新环境、重新生成入口和服务，旧 `venv` 不再使用。

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

```bash
cp fork/runtime/locales/*.yaml ~/.hermes/locales/      # 从开发副本复制；目录不存在先建
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=url.https://github.com/.insteadOf GIT_CONFIG_VALUE_0=git@github.com: \
hermes update --branch custom --yes
```

- 运行目录的 `origin` 是 SSH 地址，本机 22 端口常被关闭；上面用环境变量临时改走 HTTPS，不改保存的远端配置。
- `custom` 变基后与运行目录本地分支分叉，更新流程会先把旧 HEAD 存到 `refs/hermes-update-backups/…`，再对齐到 `origin/custom`。
- 更新流程负责：安装依赖（pm）、重新生成 `~/.local/bin/hermes`、重启网关（必要时重写 LaunchAgent）。不要用无参数的 `hermes update`，它默认切到 `main`。
- Photon sidecar 的依赖在 sidecar 启动时按新的 lockfile 重装，`postinstall` 会重新打 Spectrum 补丁。

插件代码有变化时，先比较再复制：

```bash
diff -rq -x __pycache__ ~/.hermes/hermes-agent/hermes-plugins/ollo-inbox ~/.hermes/plugins/ollo-inbox
```

只有插件改了才替换已安装副本，替换前把旧副本留在备份目录；插件的数据库在 `~/.hermes/plugin-data/`，不随插件目录替换。替换后 `hermes gateway restart`。

### 4 验证

| 项目 | 方法 | 期望 |
| --- | --- | --- |
| 代码版本 | `hermes --version`；`git -C ~/.hermes/hermes-agent rev-parse HEAD origin/custom` | 版本号为新版本；HEAD 等于 `origin/custom` |
| 运行进程 | `hermes update --plan`、`gateway_state.json` 的 `code_sha` | 网关跑的是新提交，不是旧进程 |
| 服务 | `launchctl print gui/$(id -u)/ai.hermes.gateway` | running，程序来自 pm 环境 |
| 平台 | `gateway_state.json`、`hermes photon status`、`curl 127.0.0.1:8789/healthz` | Photon、API server connected；sidecar ok。`/probe` 返回 inconclusive 不能算健康证明 |
| 插件 | `hermes plugins list`、网关日志 | `ollo-*` 已加载，无报错 |
| 定时任务 | `hermes cron list` | 全部 enabled，下次运行时间正常 |
| 语言覆盖 | 用运行环境的 Python 调 `agent.i18n.t("gateway.session.auto_reset_notice")` | 返回覆盖后的文案 |
| Ollo | Ollo 仓库 `server/deploy/install.py verify`（本机、公网） | 能力、会话、历史正常 |
| 日志 | 重启后的 `~/.hermes/logs/gateway.log`、`gateway.error.log` | 没有新的 ERROR、Traceback |
| iMessage | 用户在 iMessage 里让 Hermes 发起一个二选一确认并点选 | 选择回到 Hermes，只回复一次 |

服务状态以 `launchctl`、状态文件、日志和真实消息交叉判断，不只看 `hermes gateway status`。

`hermes photon status` 会打印手机号和 Photon 项目标识，只看「已存储/已安装」这类状态，不要把输出贴进记录。

## 不在本仓库的用户插件

`~/.hermes/plugins/` 里还有不归本 fork 管的用户插件（例如 `bot-handoff`、`orca-status`）。每次升级前确认它们用到的钩子名、导入的模块和调用的 `hermes` 命令行参数在新版本里还在（钩子清单在 `hermes_cli/plugins.py` 的 `VALID_HOOKS`），升级后看日志里有没有加载失败。

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
