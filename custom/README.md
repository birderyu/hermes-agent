# birderyu/hermes-agent 个人 fork

本目录是 `custom` 分支的说明书和台账。`custom` 分支就是 Mac mini 上实际运行的 Hermes Agent：官方正式发行版，加上少量个人补丁和 Ollo 的服务端插件。这里记录这个 fork 怎么修改、怎么升级、怎么部署，以及每次修订的经过。维护 fork 只看这里。

目录和分支同名，沿用下游在源码里用自己名字建目录的惯例（例如 Debian 的 `debian/`）。只在 `custom` 分支上存在的说明、补丁清单和修订记录都放在这里；插件源码另放在 `hermes-plugins/`。2026-10-11 之前这个目录叫 `fork/`。

官方仓库：[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)。仓库根目录的 `README.md`、`AGENTS.md` 等是官方文件，不在这里改。

## 现状

| 项目 | 值 |
| --- | --- |
| 官方基线 `main` | `v0.22.1`（`297552a9d6`，2026-10-15 发布；2026-10-15 升级，部署待 ollo-bridge 适配） |
| `custom` | `main` + 2 个 Hermes 补丁（cron）、Ollo 插件、维护文档，见 [补丁清单](patches.md) |
| 运行版本 | 以 `hermes --version` 为准，部署经过见 [记录](records/README.md) |
| 开发副本 | `~/Projects/hermes-agent`（独立 clone） |
| 运行目录 | `~/.hermes/hermes-agent`（只部署） |

## 目录

| 位置 | 内容 |
| --- | --- |
| [maintenance.md](maintenance.md) | 维护方式：分支、开发环境、新增补丁、发行版升级、测试与基线对照、回滚 |
| [deploy.md](deploy.md) | 部署到 Mac mini：运行环境布局、步骤、验证、回滚 |
| [patches.md](patches.md) | 现行补丁与已移除补丁：为什么、上游状态、何时移除 |
| [records/](records/README.md) | 修订记录 |
| [Ollo GTD 正式切换方案](records/2026-10-14-ollo-gtd-production-switch.md) | 待执行：真实任务/清单切换、离线 plan/apply/rollback、授权节点、验证与回滚 |
| [../hermes-plugins/](../hermes-plugins/README.md) | Ollo 服务端插件源码 |

## 规则

- `main` 只放官方发行基线；个人改动只在 `custom`，一个问题一个提交，并在 [补丁清单](patches.md) 登记退出条件。
- 只跟官方正式发行版升级，不追 `upstream/main`，不降级。
- 在开发副本改代码、测试、推送；运行目录只用 `hermes update --branch custom` 部署，不在里面改文件。
- 推送变基后的 `custom` 只用带精确旧 SHA 的 `--force-with-lease`。
- 这是公开仓库：不提交凭据、`.env`、OAuth、电话号码、Photon 项目标识、允许名单、会话内容、实际位置和运行数据库。
- 部署、重启网关、改 Mac mini 上的配置，先说明影响和回滚方法。
