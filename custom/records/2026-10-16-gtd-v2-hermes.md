# 2026-10-16 · G4 GTD v2 Hermes 开发候选

## 授权与边界

用户授权实现 GTD v2 的 Hermes 采集、对话上下文、候选技能和四个定时任务提示词。以 Ollo `docs/architecture/personal-harness/gtd-v2.md`、设计 README 及 G1 未提交的 openapi/bridge.openapi 为准。所有源码与候选改动仅在 `hermes-plugins/ollo-bridge/`；本记录和索引按指定路径新增。无提交、推送、部署、重启；无 Hermes 核心或 Ollo 仓库修改。

开发目录已核实为独立 clone，custom 分支。开始时已有 `custom/records/2026-10-15-v0.22.1.md` 工作区修改，保持原样。只读线上两份指定 SKILL.md 及四任务指定 prompt；未读取真实密钥、bridge.json、其他线上文件、Apple 数据或知识库笔记。未写 `~/.hermes`。

## 改动文件与行为

| 文件（相对 hermes-plugins/ollo-bridge） | 结果 |
| --- | --- |
| `collector.py`、`scripts/ollo_collect.py` | 当前Profile认证读网关，先日账跳过，再计划/位置/helper；合并日期、星期、workday、plan、地点年龄、可用旧报告锚点；stdout 一份完整JSON，失败不伪造空数据 |
| `apple_config.py`、`config.py` | agenda/review 全事件日历范围；旧三日历读取配置兼容为 all；新增 knowledge.places 单一绝对路径 |
| `native/EventKitBridge.swift` | 读全部可读事件日历，仍核对三个可写目标的唯一性；事件 calendar/location/all_day/subscribed/start/end；inbox 当天完成及任意清单计划引用；missing_plan_item_ids；同次 originals 含完成项；原生离线自检 |
| `day_context.py`、`places.py` | 计划/日账/最新位置/报告读取，单笔记地点匹配；当前绑定对话6000字符资料预算 |
| `integration.py` | 保留既有身份与确认回合拦截；只给 Ollo 绑定普通回合追加紧凑本轮资料，restricted 回合跳过资料注入及其读取；不改历史/系统提示/工具集合 |
| `schedule.py` | 为 v2 agenda 确认精确读取锚定报告并核对版本；v1仍拒绝；继续走原 claim/helper/finish，不增加执行工具 |
| `contracts/schema.json`、`contracts/export_schema.py`、`contracts/.gitignore`、`contracts/README.md` | 导出当前G1引用闭包，OlloCards/DayPlan直接入口；exporter原被根export*忽略，精确取消忽略以便后续审阅提交；记录源合同摘要 |
| `test_collection.py`、`test_focus.py`、`test_gtd_v2.py` | 采集/计划/位置/工作日/gate/上下文预算与绑定/fail closed/v1拒绝v2执行/候选schema行为测试 |
| `docs/gtd-skill-v2-candidate.md` | 完整 gtd 替换候选，四时刻、安排空档、估时/路上、记忆与位置、说话方式、拼卡规则及完整例子 |
| `docs/hermes-plus-gtd-v2-candidate.md` | 报告v2 items/originals锚点、卡片v1载体、真实确认与回执；保留直接对话Apple规则 |
| `docs/cron-prompts-v2-candidate.md` | 指定四个任务完整新prompt；零工具、一句话+卡片、晚上收尾、每周以对话为主 |
| `docs/gtd-v2-install.md`、`README.md` | 接口/形状/笔记/预算、已知限制、安装回滚与授权节点 |

三个小入口 `ollo_collect_agenda.py/inbox.py/review.py` 无需改动：共用加载器按自身安装位置确定所属home。脚本运行时需要该home的bridge凭据以及PM环境中的aiohttp/jsonschema；不把凭据放参数或输出，本轮未执行真实凭据读取。

## 采集与写入锚点

事件窗口：agenda 上海当日00:00–次日00:00，review采集时刻起七天；两者读取全部EventKit可见事件日历，包含只读/订阅。全局读取权限不足仍失败。写目标仍为工作/生活/家庭，外部日历事件不能因被采集而获得修改权限。

inbox = 默认收件箱未完成项 + 当天所有清单完成项 + 当天接受计划引用的所有事项（任意清单/完成日期）。不存在的计划ID另列，不能推断状态。每个有modified_at的返回项保存同次完整originals，包括completed=true；事件额外的显示字段不塞进原值。报告v2必须在items中显式列出待引用项，再复制其originals；originals本身不能扩权。500项/16MiB上限继续失败，不做静默截断。

workday看订阅全天事件与当日重叠：明确补班/上班、假期/休息等作为证据，冲突为null；没有证据标weekday_fallback。生日/普通纪念日不自动改变工作日。它不重写cron调度。

agenda首先GET当日日账，只有generated且日期/UUID等通过合同校验才输出末行wakeAgent=false，复用宿主_parse_wake_gate跳过模型/投递；triggered仍继续。并发先读不构成锁，最终唯一性仍由网关事务保证。跨午夜延迟wake的原业务日期未向script传入，仍是G5需要验证的既有限制。

## 地点笔记与上下文

给设计会话与用户确认的格式：单篇UTF-8 Markdown表格，表头 `名称 | 地址 | 纬度 | 经度 | 半径米`，WGS84十进制度，名称1–12字且唯一、半径(0,10000]米，最多100行/64KiB。通过bridge.json的knowledge.places指定绝对路径。本轮未创建/读取笔记；用户确认路径和内容后由设计会话写入。示例及全部细节见[安装文档](../../hermes-plugins/ollo-bridge/docs/gtd-v2-install.md)。

只读指定笔记，不跟链接，不读其他知识库。用球面距离+精度圆保守匹配，输出地点名、采样时间、age_minutes；不输出坐标/地址/笔记正文。缺失/坏格式/无匹配返回place=null。

Ollo上下文只在原有Binding校验通过后读取：当前时间/星期、已接受计划行与card_id、今日agenda/最近inbox/review的report_id及允许引用事项（含id/title/list/due/completed，事件起止）、最新地点与时间。动态JSON≤6000 Unicode字符，含约束/确认ID通常总计<7000；超限删整条次要数据并标omitted/truncation，不切ID/JSON，不把摘要当originals。最新20份报告查不到的更早类型明确标未查；今日agenda按日账ID补取。完整对话替换链的最新节点没有G1读取接口，接受计划card_id与报告plan卡ID明确分开，不冒充所有对话最新节点。

## 初版合同缺口（G4b 已补齐）

初版缺少 complete 操作及报告卡片锚定自身机制，因此当时使用改标题示例和入库后再确认的降级方案。用户后续确认 Ollo 合同和网关均已补两项，并授权 G4b 跟进。当前候选已移除这两处 TODO，使用 complete 和报告省略 anchor 的新合同；具体改动及验证见下文 G4b。完整四时刻端到端验收仍由设计会话完成，本会话未验证线上网关实现。

## 验证与证据边界

- 已执行用户指定完整命令。activate和runner的再激活都因默认uv缓存 `/Users/birderyu/.cache/uv/sdists-v9/.git` 的 `Operation not permitted` 退出；Python套件没有启动。
- 为遵守只读指定线上文件范围，创建 `/private/tmp/gtd-v2-run_tests.sh` 临时副本，仅固定原scripts目录并去掉真实home的pytest_live_guard加载；不修改核心runner，保留clean env/per-file/PM解释器机制。显式使用现有隔离test-environment Python复核三个相关文件，子进程均 `PermissionError(1, 'Operation not permitted')`，**0 collected / 0 passed；不是通过**。未用裸pytest。后续新增断言同样尚未实际运行。
- Swift：`swiftc -O -assert-config Debug`、独立module cache、Info.plist嵌入、ad-hoc签名和strict验证成功；`--self-test`返回 `{"ok":true}`。新自检覆盖全部可读日历选择/写目标歧义、当天完成边界/跨清单计划项、缺失引用、新collect形状与completed originals。未创建EKEventStore、未申请权限或读写真实Apple。产物在开发缓存 `gtd-v2/`，未部署。
- 从当前G1导出后，独立离线schema校验 **9份候选ollo-card示例通过**；4份旧报告/失败报告/context/confirmation载荷仍通过。schema合法不证明锚点存在、替换链有效或真实按钮执行。
- Python AST语法检查49个文件通过；git diff --check通过。未运行真实cron、Gateway、模型、推送、定位或iCloud联调。

沙箱外必须补跑（保持用户指定命令）：

```bash
bash -c 'export HERMES_RUNTIME_DIR=$HOME/.cache/hermes-agent-dev/tools HERMES_HOME=$HOME/.cache/hermes-agent-dev/home; source ./activate; scripts/run_tests.sh -j 2 hermes-plugins/ --file-retries 0'
```

标准runner有真实home的live guard探测；若设计会话仍被限制为仅指定文件读取，需先处理这项权限范围，详见安装文档。测试失败不能用自检/schema检查代替。

## 同日回归修复

设计会话反馈沙箱外运行 `hermes-plugins/`：27 文件、563 过、8 失败。这是修复前的外部反馈，本会话未把它计作修复后的测试结果。

- `integration.py`：restricted 回合在 Binding.start 和可信确认 ID 处理后立即返回，不进入 conversation_context，也不因上下文访问日账、计划、报告、位置或知识库。授权的 execute_gtd_confirmation 仍按原链执行。
- `test_session_authority.py`：保留原六项工具拒绝测试及其断言；新增 router/http × UUID 存在/缺失四种情况，要求确认回合无当天资料、无网关请求，下一普通回合恢复资料读取。
- `test_datetime_contract.py`：失败来自过时的 helper **输出**夹具缺少 `missing_plan_item_ids`，并非 bridge.json 配置校验。补齐合法与非法时间夹具，非法时间断言精确到 `invalid_collection_time`，防止被形状校验提前拒绝而假通过。保留 collector 输出校验。
- `test_collection.py`：新增 agenda/inbox/review 三项兼容回归，用不含 collection/knowledge 的临时旧配置经过真实 read_config 和 collector.main、合成网关与 helper 输出完成采集，检查缺省范围、place=null 和配置文件字节不变。无需新增线上配置字段；新版插件与 helper 仍需配套部署。
- 三份 v2 候选及安装说明标明两处合同 TODO；未修改 schema、Apple 执行协议或 Ollo 仓库。

修复后尝试通过上一轮的临时 runner 副本运行上述三个测试文件（保留官方隔离，仅去掉未授权的线上 guard 加载），仍全部在启动子进程时遇到 `PermissionError(1, 'Operation not permitted')`：**0 collected / 0 passed，未验证通过**。未改核心 runner，未用裸 pytest。49 个 Python 文件 AST 语法检查和 `git diff --check` 通过。

沙箱外定向复测命令：

```bash
bash -c 'export HERMES_RUNTIME_DIR=$HOME/.cache/hermes-agent-dev/tools HERMES_HOME=$HOME/.cache/hermes-agent-dev/home; source ./activate; scripts/run_tests.sh -j 2 hermes-plugins/ollo-bridge/test_session_authority.py hermes-plugins/ollo-bridge/test_datetime_contract.py hermes-plugins/ollo-bridge/test_collection.py --file-retries 0'
```

定向复测后用上面的全量命令复跑 `hermes-plugins/`。这次回归修复未改 Swift helper；其后 G4b 的修改和编译结果另记如下。

## G4b：complete 与报告卡片锚定自身

沿用原边界，只修改 ollo-bridge 与本记录，未部署、重启、提交、推送或修改线上 home。只读 Ollo 的两份新合同与合成 examples；没有更改 Ollo 仓库，没有读取真实密钥或 Apple 数据。

| 文件 | G4b 改动 |
| --- | --- |
| `contracts/export_schema.py`、`schema.json`、`contracts/README.md` | 按维护流程重导出 121 个定义；新增显式 OlloReportCardsInput 根，保留消息 OlloCards 强制写入 anchor；记录两份源合同 SHA-256 |
| `fixtures/gtd-v2/*.json` | 同步 Ollo 6 份合成样例：complete confirmation/template/report/delivery/result，以及省略 anchor 的报告卡片输入 |
| `schedule.py`、`apple.py` | 确认组在 claim 前拒绝 complete 的事件、已完成或缺失原值；backend 同样拒绝这类许可，不启动 helper；仍复用原许可、幂等回执和依赖组执行 |
| `native/EventKitBridge.swift` | helper 对 complete 原值先校验，继续核对整组当前快照；设置 isCompleted=true 并 save(commit:false)，与其他改动同组一次 commit；不扩展循环提醒支持 |
| `apple_diagnostic.py`、Swift MutationContext | 诊断操作白名单增加 complete，fields 为空；不增加事项内容/ID 日志，prepare/commit 失败语义不变 |
| `test_completion.py`、`test_apple_diagnostic.py`、`test_apple_contract.py`、`test_gtd_v2.py` | 单项/同组完成、原值拒绝、真实回执与重放、诊断清洗、schema 输入边界、三份候选按报告/对话 schema 分别验证 |
| 三份 v2 候选、`docs/gtd-v2-install.md` | 去掉两处合同 TODO；「交好了」「已经还了」用 write + complete；报告卡片全部省略 anchor，由网关入箱补齐；对话写入卡显式使用上下文 report_id；首次晚上直接 confirm |

生成时原值必须为 source=reminder、completed=false，且 item 属于允许引用集；只是 schema 合法不授权。执行前已完成/删除/原值变化时整组 skipped。暂存失败保持 not_executed；commit 失败保持 unknown，不重写、不冒称回滚。换清单组合沿用既有 AppleScript move 的外部写入边界。

G4b 验证结果：

- 按 contracts/README 的导出器读取 Ollo 新合同，离线 schema 自身校验通过。9 个候选 ollo-card 示例通过，其中 6 个报告输入省略 anchor，补合成报告 ID 后也通过 OlloCards；6 份新合同样例通过。
- Swift 使用独立开发缓存编译，包含 Info.plist、`-O -assert-config Debug`；ad-hoc 签名、strict 验证、`--self-test` 均通过，输出 `{"ok":true}`。完成自检覆盖未完成原值、缺失/事件/已完成原值拒绝、同组 update→complete 暂存一次 commit、失败 reset 与 not_executed/unknown 分类、重复/循环完成拒绝、诊断白名单。自检无 EKEventStore、无真实 Apple 读写。
- 官方 runner 的既有临时隔离副本尝试 `test_completion.py`、`test_apple_diagnostic.py`、`test_gtd_v2.py`、`test_apple_contract.py`，四文件均在创建子进程时 `PermissionError(1, 'Operation not permitted')`：**0 collected / 0 passed，不是通过**。未绕过 runner 跑 pytest；完整套件由设计会话在沙箱外按上面的原命令运行。
- 50 个 Python 文件 AST 语法检查和 `git diff --check` 通过。未做真实 Gateway/App/iCloud 验收。

完整复测命令仍为：

```bash
bash -c 'export HERMES_RUNTIME_DIR=$HOME/.cache/hermes-agent-dev/tools HERMES_HOME=$HOME/.cache/hermes-agent-dev/home; source ./activate; scripts/run_tests.sh -j 2 hermes-plugins/ --file-retries 0'
```

## 部署与回滚

本次不执行。设计会话先完成沙箱外测试及 G1–G3 合成联调，然后用户另行同意：部署插件/helper/脚本/两个技能/四任务prompt；知识库单篇笔记与knowledge.places；Gateway重启和真实Apple验收。安装步骤、备份范围、版本配套和回滚详见[G4安装说明](../../hermes-plugins/ollo-bridge/docs/gtd-v2-install.md)。回滚整套恢复备份，不删除报告/计划/幂等回执，不自动撤销真实Apple写入，不重试unknown。
