# 2026-08-23 建立个人 fork 与定制分支

## 做了什么

Mac mini 上的 Hermes 原来是直接跟官方仓库的浅克隆。这天改成个人 fork 的长期维护结构，没有重装 Hermes 数据目录，也没有改动 Photon、模型授权、代理、人格、Skills 和定时任务的配置。

- 补齐完整 Git 历史；远端改为 `origin`（个人 fork）和 `upstream`（官方）。
- `main` 与官方同步，只放官方基线；新建 `custom` 作为实际运行分支，并推送到 fork。
- 迁移基线为 `933c209e`（Hermes 0.20.5）。刷新 editable 依赖后重启网关，launchd 为 running，Photon 配置、遥测关闭状态和定时任务均核对无误。

旧工作区里有两个只差大小写的 contributor 文件路径，在 macOS 默认不区分大小写的文件系统上无法得到干净的工作区；新版官方已删除这两个路径。旧的本地修改没有搬到 `custom`，而是保存在运行目录的两个 stash 里：`pre-fork-migration-20260823`、`pre-fork-case-collision-20260823`。确认不再需要前保留，查看用 `git stash show -p`，不要直接 `pop` 到新版。

## 当天加入的补丁

- `28607d6e` fix(i18n): localize gateway session reset notices —— 自动会话重置提示与会话信息块本地化；去掉误导性的「对话历史已清除」，说明上一会话仍保存在历史里、可以用 `/resume` 恢复。三个针对性测试文件共 56 项通过；重启后网关运行该提交，Photon 已连接。未主动触发真实会话重置。
- `1e8719d74e` fix(photon): accept empty iMessage poll titles，见 [Photon 原生投票](2026-08-23-photon-poll.md)。

## 这天定下的维护规则

- `main` 不提交个人修改；每个修复或增强在 `custom` 上单独一个含义清楚的提交，方便上游修好后单独移除。
- 普通个人补丁出问题用 `git revert`，不改写已共享的历史；变基冲突先 `git rebase --abort` 再分析。
- 服务状态以 `launchctl print`、网关日志、Photon 连接和真实消息交叉判断，不能只看 `hermes gateway status`（部分版本会误报未加载）。

升级策略在 2026-08-24 改为只跟官方正式发行版，见 [开发主线同步](2026-08-24-upstream-main-sync.md)。
