# 2026-10-11 停用 iMessage：撤回 Photon 补丁，删除语言覆盖，清理旧工作副本

用户决定以后不再使用 iMessage。据此做了三件事：撤回三个只为 iMessage 服务的补丁；不装会话自动重置插件，并删除跟它配套的语言覆盖；顺带清理一个已经没用的旧工作副本。

## 撤回 3 个 iMessage 补丁

| 提交 | 内容 |
| --- | --- |
| `b0958faafa` fix(photon): accept empty iMessage poll titles | 空投票标题的 Spectrum 补丁 |
| `2f360fa07c` fix(photon): scope native poll lifecycle to clarify | `clarify` 原生投票的生命周期与多选（v0.21.6 上重写的版本） |
| `db496d81b7` fix(photon): align voice attachment filename with m4a | 语音附件 `.m4a` 文件名 |

- 方式：在 `custom` 上 `git revert`，提交为 `revert(photon): drop iMessage patches`，不改写已推送的历史。下次发行版变基时，原提交和撤回提交一起丢掉。
- 结果：`plugins/platforms/photon/` 和 `tests/plugins/platforms/photon/` 与官方 v0.21.6 逐字节一致，本 fork 不再改 Photon。
- 测试：隔离开发环境里 `scripts/run_tests.sh tests/plugins/platforms/photon/`，23 个文件，149 passed、0 failed、1 skipped。sidecar 回到上游后没有 `npm test` 脚本（原来的测试入口是补丁加的）；`npm ci` 正常，`postinstall` 只打上游自带的 Spectrum 补丁。
- 重写后的投票生命周期始终没有做真机验证，现在不再需要。
- 部署：撤回要部署后才生效。在那之前，运行中的网关仍带着这三个补丁；不用 iMessage 时没有影响。

## 不装会话自动重置插件，删除语言覆盖

- 闲置 24 小时、每天 4 点自动开新会话的机制，v0.21.6 起移到插件 `hermes-session-reset-policy`。旧代码里它只作用于消息渠道的会话（`gateway/session.py` 的 `SessionStore`）；API server 的会话由客户端指定，不经过它，所以 Ollo 本来就不受影响。不用 iMessage 后，决定不装。
- 语言覆盖只改写自动重置时的提示文案，随之删除：仓库里的 `custom/runtime/` 删除；运行环境的 `~/.hermes/locales/zh.yaml`、`en.yaml`（和仓库版本一致）移到 `~/.hermes/backups/cleanup-20261011/locales/`，空目录 `~/.hermes/locales` 删除。回滚：把两个文件移回去。
- `config.yaml` 的 `session_reset` 块没动，网关启动和 `hermes doctor` 仍会提示它不再生效。

## 清理旧工作副本 2f00

- `~/.codex/worktrees/2f00/hermes-agent` 是 Codex App 在 10-08 建的工作副本，挂在运行目录的仓库上，分支 `ollo-plugins`（`e885280c79`）。线上「语气开关」的改动一直以未提交状态放在这里。
- 设计会话已把这份改动并进开发副本，见 [Ollo 语气开关归并](2026-10-11-ollo-mood-toggle.md)。逐个文件核对：`__init__.py`、README、`test_conversation.py` 完全相同；`test_mood.py` 只差 v0.21.6 事件流的适配，测试函数一个不少；线上已部署的 `__init__.py` 也与之相同。
- 删除前把未提交的改动存成 `~/.hermes/backups/cleanup-20261011/2f00-ollo-mood-toggle.patch`，分支末端记在同目录的 `2f00-ollo-plugins-branch-tip.txt`；`e885280c79` 也包含在运行目录的 `backup/custom-pre-v0.21.6-20261011` 里。
- 用 `git worktree remove --force` 移除工作副本，删除分支 `ollo-plugins`。运行目录的检出和网关不受影响。
- 回滚：`git -C ~/.hermes/hermes-agent worktree add -b ollo-plugins ~/.codex/worktrees/2f00/hermes-agent e885280c79`，再在其中 `git apply` 那份补丁。

## 其他

zsh 下 `source ./activate` 会失败：`scripts/_activation.sh` 查找 Windows 版 Python 的通配符（`python-*/python.exe`）匹配不到，zsh 报 `no matches found`。改在 bash 里执行，已写进 [维护方式](../maintenance.md#开发环境)。

## 遗留

- 4 个 GTD 定时任务仍然投递到 iMessage（Photon）。等 Ollo bridge 完成后改投 Ollo，之后可以关掉网关的 Photon 平台（要改 `~/.hermes` 的配置）。
- 撤回提交待部署，可以和 Ollo bridge 的部署合并，少重启一次网关。
- 可选：删掉 `config.yaml` 里的 `session_reset` 块，消除启动提示。
