# 2026-10-11 部署 ollo-bridge（Ollo 网关第 2、5 步）

经用户同意，由 Ollo 设计会话执行。同一次部署带上了 iMessage 停用时撤回的 3 个 Photon 补丁（用户在 Ollo 会话中确认「同意一起部署」）。

## 版本

- 运行目录 `67d3c0b3` → `23cc01830d`（`hermes update --branch custom --yes`，按 [部署](../deploy.md) 走 HTTPS）。
- 包含：`1bbb986596` 投递重试补丁、`14f2be2480` ollo-bridge 与语气开关、`e596821fad` 撤回 Photon 补丁，以及若干文档提交。

## 过程

| 时间 | 动作 | 结果 |
| --- | --- | --- |
| 21:14 | 预检 | 运行目录干净，只有 origin，`auto_switch_parked_branch=false`，LaunchAgent 经 `/usr/bin/osascript` 运行，Hermes 桌面 App 未运行 |
| 21:14 | 备份到 `~/.hermes/backups/pre-ollo-bridge-20261011` | config.yaml、.env（只复制）、plist、启动器、plugins、jobs.json、skills/gtd、ollo/；六个库和旧插件数据库 `inbox.sqlite`、`latest.sqlite` 用 `.backup`，完整性检查通过；运行目录建分支 `backup/custom-pre-ollo-bridge-20261011` |
| 21:15:05 | 更新并重启（第 1 次） | `code_sha=23cc01830def`；photon、api_server、hermes_plus、ollo 均 connected；21:15 后无新 ERROR |
| 21:16 | 安装 | `~/.hermes/plugins/ollo-bridge`（与运行目录 `hermes-plugins/ollo-bridge` 一致，去掉 `__pycache__`）；`~/.hermes/scripts/ollo_collect*.py`；`~/.hermes/ollo/bin/ollo-eventkit`（swiftc -O，嵌入 `native/Info.plist`，ad-hoc 签名，`--self-test` 通过）；Skill `hermes-plus-gtd`、`gtd-ollo-test` |
| 21:16 | 测试任务 | 用 `cron.jobs.create_job` 新建 4 个「Ollo测试-GTD-*」任务：时刻、模型与真实任务相同，`deliver=ollo:main`、`enabled_toolsets=[]`、`attach_to_session=false`、skills=`gtd-ollo-test`+`hermes-plus-gtd`，script 为对应采集入口。真实任务未改，仍投 photon |
| 21:16 | `~/.hermes/ollo/bridge.json`（600） | 根会话、网关回环地址、helper 路径；白名单与 agenda 任务只含 4 个测试任务；`apple` 对应表把六个清单、三个日历对应到「Ollo 测试·*」 |
| 21:17 | `config.yaml` | 仅改插件段：`plugins.enabled` 去掉 ollo-conversation、ollo-inbox、ollo-location，加 ollo-bridge；删除 `plugins.entries.ollo-location` |
| 21:17:20 | `hermes gateway restart`（第 2 次） | LaunchAgent 仍为 osascript；ollo-bridge 已挂接 API 处理器，ollo、photon connected；21:17 后无新 ERROR |
| 21:17:51 | Ollo 网关 | 白名单为 4 个测试任务，inbox/push/location/schedule 打开并重启；`verify` 显示四项为 true，`push_status=ready`，对话与历史正常 |

旧插件目录和 `plugin-data` 原样保留，未导入、未删除。

## 没有验证到的

- osascript 的日历/提醒事项授权在 21:00 那次运行时仍未生效（见 [v0.21.6 记录](2026-10-11-v0.21.6.md)），要看 10-12 08:00 两个今日安排任务的自然运行。ollo-eventkit 由网关启动，沿用同一授权。
- 采集、报告投递到 Ollo、items/interpret、确认写入、锁屏推送、随手记、wake、定位，都要等 Ollo App 0.1.0 (9) 装上后在真机上联合验收。

## 回滚

恢复备份中的 `config.yaml`，删除 `~/.hermes/plugins/ollo-bridge`、两个新 Skill、4 个测试任务（`hermes cron rm`），`hermes gateway restart`；Ollo 网关用 `install.py configure-features --disable …` 关掉四项并重启。代码回滚按 [部署](../deploy.md#回滚) 用 `backup/custom-pre-ollo-bridge-20261011`。
