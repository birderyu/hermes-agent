# 2026-10-15 关闭 iMessage（Photon）通道

用户在 Ollo 会话中决定去掉 iMessage 通道和相关插件（GTD 已于 10-14 切到 Ollo，见 [正式切换](2026-10-14-ollo-gtd-production-switch.md)；10-11 已撤回 Photon 补丁，见 [停用 iMessage](2026-10-11-retire-imessage.md)）。由 Ollo 设计会话执行，只改运行目录配置，不改 fork 代码，不碰密钥。

| 时间 | 动作 | 结果 |
| --- | --- | --- |
| 11:10 | 预检、备份 | 无在途任务，下次 cron 21:00；`config.yaml` 与 care、gold、wiki 三个档案的 `config.yaml` 备份到 `~/.hermes/backups/retire-imessage-20261015-111011` |
| 11:10 | 主档案 `config.yaml` | `platforms.photon.enabled: false`（保留配置块便于恢复）；`plugins.enabled` 去掉 `bot-handoff`（只处理 photon 事件，第 235 行限定） |
| 11:10 | care、gold、wiki 档案 | `platforms.photon.enabled: false`。这三个档案本来就没在运行（当前网关只服务主档案）；它们启用的 `bot-handoff` 未改，无 photon 时不起作用 |
| 11:10 | 旧插件 | `ollo-conversation`、`ollo-inbox`、`ollo-location`（10-11 起停用）移到备份目录的 `plugins/`，未删除；`plugin-data` 未动 |
| 11:10:31 | `hermes gateway restart` 一次 | 重启后只连 hermes_plus（Ollo 旧名，ollo-bridge 注册）、ollo、api_server；8789 无监听，Photon sidecar 已停；Ollo 网关本机、公网 `verify` 通过 |

未做：`.env` 里的 `PHOTON_PROJECT_ID` / `PHOTON_PROJECT_SECRET`（含三个档案）由用户自行删除；`skills/apple-calendar/SKILL.md` 第 39 行仍有一句 iMessage 确认方式的说明，无影响。`hermes_plus` 平台保留：它是 ollo-bridge 注册的 Ollo 旧名，不是 iMessage。

回滚：把备份里的四份 `config.yaml` 复制回原处、旧插件目录移回 `~/.hermes/plugins/`，`hermes gateway restart`。

## 补充：停用 photon 插件、关闭 hermes_plus 旧通道（17:59）

用户同日要求 photon 插件也不再需要，并同意一起关掉 `hermes_plus`。改前 `config.yaml` 另存为备份目录里的 `config.yaml.before-photon-plugin`。

- `hermes plugins disable photon-platform`：`plugins.disabled` 增加 `platforms/photon`，插件不再加载（`hermes plugins list` 显示 disabled）。插件本身是 Hermes 自带代码，不能卸载；`plugins/platforms/photon/sidecar/node_modules`（113 MB，未纳入版本库的安装产物）留在运行目录。
- `platforms.hermes_plus.enabled: false`。核对过：`cron/jobs.json`、`ollo/bridge.json` 无 `hermes_plus`；`state.db` 的会话来源里没有 hermes_plus，Ollo 对话走 `api_server`，报告走 `ollo`。
- `hermes gateway restart` 一次：只连 `ollo`、`api_server`；Ollo 网关 `verify` 对话与历史通过。

后续（可选）：fork 里 `ollo-bridge/__init__.py` 仍同时登记 `ollo` 和 `hermes_plus` 两个平台名，以及 `HERMES_PLUS_HOME_CHANNEL`；下次改 bridge 时一并删掉旧名。回滚：恢复 `config.yaml.before-photon-plugin` 后重启。
