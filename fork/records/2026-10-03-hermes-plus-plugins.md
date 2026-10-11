# 2026-10-03 迁入 Hermes+ 服务端插件源码

把 Hermes+ App（后改名 Ollo）的两个服务端插件从客户端仓库迁进本 fork 的 `custom`，由 fork 统一维护。插件是用户确认可以公开后才提交的。

| 项目 | 内容 |
| --- | --- |
| 源 | 客户端仓库 `birderyu/hermes-plus`（私有）的 `server/`，迁移前 HEAD `f2bc57d` |
| 目标 | 本仓库 `hermes-plugins/hermes-plus-inbox/`（1.0.0）、`hermes-plugins/hermes-plus-location/`（1.1.0） |
| 提交 | fork `50d4151c6e`；客户端仓库删除 `server/` 并更新引用为 `bf01966` |

## 做法

- `rsync` 复制两个插件目录共 16 个文件，排除 `__pycache__`、`*.pyc`、`*.sqlite`、`.DS_Store`、`.p8`、私钥和环境文件；`diff -r` 无差异。
- 只改了两个 README（来源、路径、测试命令、Python 版本要求）；其余 14 个文件逐字节一致，插件名、版本、manifest、HTTP 路由、数据路径都没变。
- 仓库根 `.gitignore` 的 `hermes-*/*` 会隐藏新源码，所以加了这两个插件目录的例外，并忽略其中的 `*.sqlite`、`*.p8`、`*.key`。
- `hermes-plugins/` 只放源码，Hermes 的插件发现不会扫描它；运行中的插件在 Hermes home 的 `plugins/`，复制安装。

## 验证

默认 `python3` 是 Xcode 的 3.9，插件的类型注解语法加载失败；改用 Python 3.12 做迁移前后对照，结果一致：收件箱 17 项通过；位置 54 项中 51 通过、3 跳过（缺 `aiohttp` 的 loopback HTTP 合同测试）。没有做完整 Hermes 回归和线上验收。

迁移时运行目录里已有一处未提交的调度器修改（`cron/scheduler_delivery.py` 透传 `execution_id`）和它的 `.bak` 备份，本次没有动它们；调度器修改后来在 2026-10-08 单独提交，见 [Ollo 插件归并](2026-10-08-ollo-plugins-and-cron.md)。

## 后续

2026-10-08 插件改名为 `ollo-*`，新增 `ollo-conversation`。2026-10-11 清理了根 `.gitignore` 里只针对旧名字的例外，改由 `hermes-plugins/.gitignore` 负责。
