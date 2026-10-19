# 2026-10-20 Ollo 服务端代码移到 Ollo 仓库

用户 2026-10-15 决定：Ollo 对 Hermes 的非本体改动（ollo-bridge 插件、日历助手、GTD 定时任务的提示词和采集脚本、gtd 与 hermes-plus-gtd 技能、正式切换等部署工具）迁回 Ollo 仓库新的顶层目录 `hermes/`；本 fork 只留 Hermes 本体补丁和 `custom/` 维护记录。已停用的 `ollo-conversation`、`ollo-inbox`、`ollo-location` 不迁，留在 git 历史里。方案见 Ollo 仓库 `docs/architecture/code-structure.md` 第 6.2 节。

## 分工与经过

| 步骤 | 内容 | 执行 |
| --- | --- | --- |
| 准备 | 等跨清单移动修复（10-15）部署验收；用户选「先完成 GTD v2 部署与验收，再迁移」，迁移期间 fork 的 `hermes-plugins/` 冻结，只允许验收修正，先进 fork 再同步 | 用户决定 |
| 1 | Ollo 仓库规则与文档：顶层加 `hermes/`；迁移完成前线上以 fork 为准 | 设计会话 |
| 2 | Codex 从 fork `66a24c14de` 只读复制代码和测试，接上 `check.sh`、`compat.toml`、schema 导出和部署工具 `hermes/deploy/manage.py`；运行文件与 fork 逐字节一致；Ollo 仓库迁移提交 `405ed17506` | 设计会话 |
| 3 | 空部署核对：只读比对 `~/.hermes`，22 个运行文件、`plugin.yaml`、两份技能、四个采集脚本、四段任务提示词逐字节一致，新编译的日历助手与线上 SHA 相同；唯一差异是 `schema.json`（多了附件错误码和 GTD 说明，bridge 的校验入口碰不到），随第一次从 `hermes/` 部署上线（Ollo `main` `0779163`） | 设计会话 |
| 4 | 本 fork 删除 `hermes-plugins/` 与插件忽略规则（`bcc45f9101`）；Ollo 仓库把 `compat.toml` 改成该提交，默认模式 `check.sh` 通过；本 fork 更新 `custom/` 文档 | 本会话；兼容检查由设计会话执行 |

## fork 侧的核对与改动

- **删除提交 `bcc45f9101`**（用户同意后普通推送）：删除 `hermes-plugins/` 的 145 个已跟踪文件，以及 `.gitignore` 第 328–334 行。删除后 `.gitignore` 与官方 v0.22.1 逐字节相同；相对 v0.22.1，`custom/` 之外只剩两个 cron 补丁的 4 个文件（`cron/delivery_queue.py`、`cron/delivery_retry.py`、`cron/scheduler_delivery.py`、`tests/cron/test_delivery_retry.py`）。
- **运行环境不依赖 fork 的插件目录**：`~/.hermes/config.yaml`、6 个档案的配置、`cron/jobs.json`、`ollo/bridge.json`、LaunchAgent、crontab 都没有指向 `hermes-plugins` 的引用；`~/.hermes` 与 `~/.local/bin` 下没有指向它的软链接；Hermes 本体代码也不读这个目录。只有已安装插件自带的说明文档提到这个路径。所以 `hermes update --branch custom` 不会影响 Ollo 的运行文件。运行目录停在 `b91c8e5a9c`，里面的旧 `hermes-plugins/` 要到下次 `hermes update` 才会删掉，不影响 `~/.hermes/plugins/ollo-bridge`。
- **兼容验收**（设计会话）：`compat.toml` 的 `fork_commit` = `bcc45f9101`，与开发副本 HEAD、`origin/custom` 一致；默认模式 `check.sh` 退出 0：27 个宿主符号和 execution_id 元数据都在，原插件测试 466 项、合计 529 项全过、0 跳过；访问旧 `hermes-plugins/` 的保护开着，没有触发；日历助手编译、签名、自检通过。
- **文档**：[补丁清单](../patches.md) 的两个 cron 补丁改为登记 Ollo 仓库里的使用方（`hermes/ollo-bridge/__init__.py`、`reports.py`、`execution.py`，宿主符号清单 `hermes/scripts/check_host.py`，兼容提交 `hermes/compat.toml`），新增「已移出的 Ollo 插件提交」一节，写明下次变基怎么处理；[维护方式](../maintenance.md) 的发行版升级加上在升级副本上跑 `check.sh --candidate`，推送后更新 `compat.toml` 再用默认模式复跑；[部署](../deploy.md) 和 [README](../README.md) 改为指向 Ollo 仓库的 `hermes/` 与 `hermes/deploy/manage.py`。`records/` 下的历史记录保留原来的 `hermes-plugins/` 路径，不改。

## 下次变基时

`main..custom` 里还有 16 个插件相关提交：9 个只改 `hermes-plugins/` 和 `.gitignore`，与删除提交合起来净效果为零，直接丢掉；7 个同时带 `custom/records/` 的记录，要保留记录、剥掉插件部分。做法和验收见 [补丁清单](../patches.md#已移出的-ollo-插件提交)。

## 没有做的

- 没有部署、没有重启，`~/.hermes` 未改动。
- 插件仍用文件同步部署（`manage.py`）。Hermes v0.22.x 的 `hermes plugins install <仓库>/<子目录> --ref <提交>` 从源码看支持子目录和钉提交，但还没实测，等 Ollo 仓库确定发布方式后再评估。
