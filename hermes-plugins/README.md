# Ollo 服务端插件

本目录维护 Ollo 服务端插件源码；实际安装位于 Hermes home 的 `plugins/ollo-*`，采用复制安装，与本目录分开。本目录不包含运行配置、数据库或密钥。

部署记录（北京时间）：

- 2026-10-08 15:48：首次部署 `ollo-inbox`、`ollo-location`、`ollo-conversation`；备份位于 Hermes home 的 `backups/ollo-deploy-20261008-1525/`。
- 2026-10-08 18:49：仅替换 `ollo-conversation` 为 `1.1.0`；主回复写完后，由宿主辅助任务 `ollo_mood` 分类语气，整体等待上限 3 秒；备份位于 `backups/ollo-mood-deploy-20261008-1848/`。

本次源码归并不重复上述部署，不替换已安装插件，不修改配置或定时任务，不重启网关。

- [ollo-inbox](ollo-inbox/README.md)：报告持久化、`ollo:main` 投递、收件箱 API。
- [ollo-location](ollo-location/README.md)：限时共享、按需设备位置、可选 APNs。
- [ollo-conversation](ollo-conversation/README.md)：只读会话发现、Ollo 身份和消息语气约定。

三者分别安装为独立插件，没有插件加载顺序依赖。两个数据插件各自携带 `migration.py`，不要漏装；同一迁移合同由参数化测试覆盖。配置与会话都属于当前 Hermes home。当前仅提供 API 原生路径，不新增 `/p/<profile>/...` 镜像路由。

## 部署清单：以下每一步均需用户另行同意

1. **确定候选版本、窗口和备份。** 对照正在运行的版本审查插件差异，核对当前活动任务、网关和 API/Photon 健康。备份旧插件、Hermes `config.yaml`、需要修改的环境变量配置和定时任务字段、`hermes-plus/` 配置、两个数据目录。在线 SQLite 用 backup API 做一致性备份；最终目录迁移前必须排空并停止所有使用这些库的网关、旧插件和 cron worker 写入者。不要一边运行旧插件一边让新插件在真实 Hermes home 下被发现/注册（注册会触发迁移）。Plugin Doctor 自身在临时 home 加载，只用于隔离检查。
2. **安装三个新插件并切换启用项。** 完整安装到当前 Hermes home 的 `plugins/ollo-inbox/`、`plugins/ollo-location/`、`plugins/ollo-conversation/`。`plugins.enabled` 中将 `hermes-plus-inbox` → `ollo-inbox`、`hermes-plus-location` → `ollo-location`，增加 `ollo-conversation`；其他插件保持原值。如使用 `plugins.entries.<name>`，相应重命名键并保留设置。旧插件移到备份目录，不可与新插件同时启用（会重复注册旧 API 路径）。自定义工具白名单中将 `hermes_plus_inbox` / `hermes_plus_location` 改成 `ollo_inbox` / `ollo_location`；工具本身 `get_gtd_reports` / `get_user_location` 不改名。
3. **准备配置、平台和环境变量。** 将旧 `hermes-plus/` 的完整配置副本放到 `ollo/`，确认 `inbox.json` 与 `conversation.json` 的 `session_id` 指向同一个既有 App 会话根 ID；不新建对话。新目录不存在才读旧目录；只创建空的 `ollo/` 会停用旧配置回退。`platforms.ollo.enabled: true`；兼容期也保留 `platforms.hermes_plus.enabled: true`，这样尚未迁移的旧目标和持久投递队列仍能走 live adapter。两个平台共享同一存储，同次投递不会生成第二份报告。APNs 将四个 `HERMES_PLUS_APNS_{KEY_PATH,KEY_ID,TEAM_ID,TOPIC}` 改成 `OLLO_APNS_{KEY_PATH,KEY_ID,TEAM_ID,TOPIC}`；每项新变量未设置时读对应旧变量，新变量为空不会回退。主题示例统一为 `com.birderyu.ollo`，须与 App 签名一致；密钥仍在外部，权限受限。
4. **迁移数据并检查。** 新数据目录缺失而旧目录存在时，首次注册复制整个旧目录到 `plugin-data/hermes-plus-<kind>.backup-<随机ID>`，完整备份成功后才原子改名为 `plugin-data/ollo-<kind>`。未完成备份以 `.partial` 结尾，不能当完整备份。迁移失败记录 warning、继续打开旧目录，不自动创建空的新库；先处理失败原因，在下次完整排空后重试。若新旧目录都存在，始终使用新目录，不合并或覆盖。插件包含 POSIX 文件锁，避免同次启动的多个发现进程相互搬迁；本套插件部署目标为 macOS。备份包含敏感设备登记和位置，按原数据的权限管理。用 `hermes plugins doctor <新插件目录> --ci` 做临时 home 注册检查；实际搬迁发生在第 6 步首次启动注册时，届时确认迁移日志、数据库记录和权限。
5. **切换现有定时任务和提示。** 通过 Hermes 加锁任务更新接口，将需要迁移的现有 `deliver: hermes_plus:main` 改成 `deliver: ollo:main`；保留任务 ID、时刻、Skill 和业务规则。仍投递 Photon 的任务只能在用户确认范围后切换。收件箱模式显式 `attach_to_session: false`。保存每个被改字段以便回滚。按 [会话约定](ollo-conversation/README.md) 给今日安排、清理收件箱、每周回顾的任务提示加相同语气规则；cron 不属于 App 会话，不会自动获得该 hook。日程围栏 `hermes-plus-gtd`、`-confirm`、`-result` 保持原名。
6. **启动并做真实验收。** 在用户授权的维护窗口通过原服务管理流程启动/重启网关。先确认 API、Photon 与新旧投递平台健康，再按下表用新旧路径各验一次；用同一个 execution ID 分别经新旧目标投递隔离报告，确认只落一份。获得同意后在 App 做普通回答、好消息、生病/焦虑话题与压缩续接验收；iMessage 不应出现 `ollo-mood`。最后再验收 APNs 及真机后台能力；APNs `accepted` 不是设备收到了通知的证据。
7. **必要时回滚；别名删除另审。** 回滚也需授权排空和停止所有写入者。先备份升级后产生的数据，再恢复旧插件、启用名、工具白名单、平台配置、任务字段和提示、旧环境变量；配置恢复到旧目录。数据结构未改变，优先将当前使用的 `ollo-*` 数据目录完整改名回 `hermes-plus-*`，保留升级后新记录；若旧目录还存在，先归档核对，绝不直接覆盖。只有确认可以放弃升级后写入时才恢复迁移前备份。恢复服务并重复旧路径/Photon 验证。所有 App 与队列都迁移完成后，删除旧路径和旧平台别名属于单独授权的后续变更。

`cron/scheduler_delivery.py` 中现有的 `execution_id` 透传补丁作为独立核心提交维护，不并入插件改名提交；本次只将已有文件内容纳入版本管理，不重新应用或回退补丁，原 `.bak` 保留且不提交。缺少有效执行 ID 会让报告投递明确失败。保留的 `ollo-inbox/compatibility.py` 仍是旧检查工具，本次不运行 `--apply`。

## 部署后新旧路径验收表

请求使用既有 API Bearer key，表中的 `$ROOT` 是配置的会话根 ID。

| 项目 | 新路径 | 旧路径 | 预期 |
| --- | --- | --- | --- |
| 会话发现 | `GET /v1/ollo/conversation` | `GET /v1/hermes-plus/conversation` | 相同 `{session_id,name}`；未配置 503；无/错密钥 401 |
| 收件箱能力 | `GET /v1/ollo/inbox?session_id=$ROOT` | `GET /v1/hermes-plus/inbox?session_id=$ROOT` | 相同版本、推送状态 |
| 报告同步 | `GET /v1/ollo/inbox/reports?session_id=$ROOT` | `GET /v1/hermes-plus/inbox/reports?session_id=$ROOT` | UUID、序号、正文完全相同；其他会话 403 |
| 位置能力 | `GET /v1/ollo/location` | `GET /v1/hermes-plus/location` | 相同能力，owner key 认证 |
| 设备状态 | `GET /v1/ollo/location/device/state` | `GET /v1/hermes-plus/location/device/state` | 同一个测试 device token 得到相同 grant；owner key 不能替代 device token |

所有子路径也保留别名，写操作应使用单独授权的测试设备/租约/报告，避免误改真实设备授权。完整接口见各插件 README。

## 沙箱验证

从 Hermes Agent 仓库根目录使用其隔离测试器：

```sh
scripts/run_tests.sh -j 2 hermes-plugins --file-retries 0
```

测试只使用临时 Hermes home、临时 SQLite 和模拟推送；通过真实 PluginManager 发现/注册、真实 APIServerAdapter 认证、SessionDB、aiohttp 路由器验证合同。不会调用模型、Apple 服务、真实任务或运行网关。测试不能证明模型一定遵循语气指令，需部署后的真实对话验收。

若沙箱禁止监听 loopback，三项原 socket 测试明确跳过；新旧路径合同仍由无监听 socket 的真实路由器测试覆盖。若测试器的 `/var/tmp/hermes-pytest` 不可写，只能在临时测试器副本中把 scratch 目录改为获准临时目录并保留源码根路径；不得改核心测试器或把受限测试说成通过。

## 本轮文件与验证记录（2026-10-08）

| 范围 | 文件 |
| --- | --- |
| 原 `hermes-plus-inbox/` 整体更名为 `ollo-inbox/` | 修改 `__init__.py`、`apns.py`、`plugin.yaml`、`README.md`、`test_inbox.py`、`integration_sandbox.py`；`store.py` 和 `compatibility.py` 仅随目录迁移、内容不变；新增 `migration.py`、`test_compatibility.py` |
| 原 `hermes-plus-location/` 整体更名为 `ollo-location/` | 修改 `__init__.py`、`apns.py`、`plugin.yaml`、`README.md`、`APNS.md`、`test_apns.py`、`test_device_location.py`；`test_location.py` 随目录迁移；新增 `migration.py`、`test_compatibility.py` |
| 新 `ollo-conversation/` | `__init__.py`、`mood.py`、`plugin.yaml`、`README.md`、`test_conversation.py`、`test_mood.py` |
| 本目录共用文件 | `.gitignore`（让新源码可见，仍忽略凭据和数据）、`conftest.py`（临时 home 与真实注册/路由测试工具）、`README.md`（部署清单与本记录） |

本次归并前，使用上述命令验证 8 个测试文件，共 **107 通过、0 失败、3 跳过**（改名阶段的历史结果为 7 个文件、93 通过、0 失败、3 跳过）。测试在 `6671dad141` 的完整已跟踪源码临时副本中执行，只将副本测试器的 scratch 路径从 `/var/tmp/hermes-pytest` 改为获准临时目录；源码根路径仍指向该临时副本，其余环境清理、按文件隔离、超时与重试机制保留。未修改维护仓库或运行目录中的核心测试器。3 项跳过源于沙箱拒绝绑定 `127.0.0.1`，不是接口验证通过。新增无 socket 合同覆盖真实路由、认证与数据库；额外覆盖三插件共同加载、多级压缩、分支/reset/委派排除、A→B→A Profile 隔离、备份失败/改名失败回退、并发迁移、配置/环境变量优先级及报告原文保留。

改名阶段曾记录：三个插件分别通过真实 Plugin Doctor，未报错误或警告；只读兼容检查返回 `patch ready; source unchanged`，当时的审查工作副本尚缺执行 ID 补丁。这些是此前阶段的验证记录，不代表本次重新执行 Plugin Doctor。运行目录已有的调度补丁单独核对、验证并入库，结果另记维护档案。

此前“未部署、重启、提交或推送”的说明仅适用于最初源码审查阶段；历史部署见本文开头。本轮仅归并源码、提交既有调度补丁并同步远端，不修改已部署插件、运行配置或真实任务，不重启网关。沙箱测试不证明辅助模型的真实分类质量、socket 服务、APNs 或真机效果，本次不重复线上验收。
