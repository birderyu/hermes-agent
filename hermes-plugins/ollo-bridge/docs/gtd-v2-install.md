# G4 安装与回滚候选

2026-10-16：本轮只改开发副本，未部署、未重启、未提交、未推送。线上仅读取指定两份技能和指定四个任务的 prompt；没有读取密钥、bridge 配置、Apple 数据或地点笔记。需要设计会话在 G1/G2/G3 合并后协调联调。

## 交付与接口

- `collector.py` + `day_context.py`：首先 GET `/internal/v1/schedule/agenda/{date}`，generated 时输出一行 `{"wakeAgent":false,"reason":"agenda_already_generated","business_date":"…","report_id":"…"}` 并返回0，helper、计划/位置读取、模型和投递都不再开始。只有经过 schema 校验、日期一致且有报告/执行 UUID 的 generated 才跳过；triggered 不跳过。失败不伪造 generated。
- 复用宿主 `cron.scheduler_prompt._parse_wake_gate` 的末尾非空行 JSON 布尔约定，不修改核心。并发检查不是锁，两次同时查到未生成仍可能分别运行模型；最终去重继续由网关事务负责。跨日延迟 wake 的历史业务日期没有被 cron script 参数传入，采集仍按实际上海日期；此旧限制须 G5 验收，不声称解决跨日触发。
- GET `/internal/v1/plans/{date}` 读取已接受计划，404 才代表无计划；其他失败使采集失败，不能悄悄丢掉计划引用项。GET `/internal/v1/location/latest` 不触发定位，失败降级为 null 并标 unavailable。每个 HTTP 请求2秒；读取操作没有重试。报告查最近20份，必要时按日账 ID 精确补取今日 agenda，最多3份报告摘要；分页截断明确标记。
- collector 不再是无认证的 stdlib 脚本。部署后由 Hermes 管理的 Python 提供 aiohttp/jsonschema，复用 read_config/GatewayClient 的当前 Profile bridge.key（0600），只用于固定 loopback 的 Bearer。禁代理/重定向，不放 CLI、stdout、日志。不读取 owner/API/模型密钥。本轮只使用合成测试凭据，没有访问真实密钥。

## collect 形状与 originals

agenda/review 的 calendars 固定为 `"all"`；旧配置中三个日历名数组继续可解析并升级为 all，显式空数组也不能缩小这两个 kind 的读取范围。全部可读事件日历包括共享、订阅和生日；EventKit 不返回的无权限日历不绕过权限。全局无事件读取权限仍失败。配置的三个写入日历必须存在、唯一且可写；写入目的地仍限工作/生活/家庭。外部事件不能因扩大采集而变成可写。

事件保留 `calendar`（实际日历名）、`location`（原字符串）、`all_day`、`subscribed`、`start/end`；`list` 仍为原逻辑名映射，用于 originals 比较。全天事件保留起止和全天标志，不推断时区外的全天边界。

inbox 选中集合 = 所选清单未完成项（默认收件箱） ∪ 所有可读清单当天完成项 ∪ 当天已接受计划引用事项（任意清单、任意完成日期）。为此仅一次 fetch 全部可读提醒，然后筛选；未找到的计划 ID 单独放 `missing_plan_item_ids`，不谎称完成。未入计划的外部清单未完成项不被带入。agenda/review 仍取所选六清单全部未完成项，加计划引用项。

helper 输出 `{items,originals,collected_at,missing_plan_item_ids}`。每个有 modified_at 的已返回项都保留同次 originals（包括 completed=true 和外部清单的计划项）；无时间不补造。原值仍只有 source/title/list/due/completed/modified_at，事件加 start/end。completed_at、地点、全天、订阅是采集展示字段，不扩大写入原值合同。500项/16MiB 硬上限仍失败，不截数据。

模型生成 gtd v2 时显式把需要展示或操作的事项列入 items，再复制其 originals；只把 ID 放 originals 不能授权。completed=true 的原值用于晚上事实展示，helper 拒绝再改已完成项。定时报告内所有卡片省略 anchor，网关入箱时补本报告 ID 并核对 items/originals；对话写入卡显式用上下文的 report_id。collector 提供的旧报告 allowed_items 仍交集到本次采集且原值完全一致，供背景与对话引用，首次报告写入卡不再依赖旧锚点。

`workday` 为 `{value: true|false|null, source, event_ids}`。只用覆盖当天的订阅全天事件：明确补班/上班优先按工作日，放假/休息/假期/调休按休息日，节假日日历中的已知节名可判休息日；矛盾为 null。没有可解释证据时按星期回退并标 weekday_fallback，生日/普通纪念日不改变它。它是计划建议依据，不修改四个任务的日历调度。

## 常去地点笔记：交设计会话与用户确认

bridge.json 新增非密钥配置：

```json
{"knowledge":{"places":"/ABSOLUTE/PATH/常去地点.md"}}
```

建议在用户指定的知识库中维护**一篇** UTF-8 Markdown；本轮不创建、不搜索、不读取知识库。设计会话先与用户确认存放路径、名称、地址、坐标、半径，再经用户同意写入。示意（纯合成坐标，不能直接安装为真实地点）：

```markdown
# 常去地点

| 名称 | 地址 | 纬度 | 经度 | 半径米 |
| --- | --- | --- | --- | --- |
| 示例地点 | 示例地址 | 0 | 0 | 200 |
```

表头固定；纬经度为 WGS84 十进制度，半径单位米，范围 (0,10000]；名称唯一且1–12字，地址非空，单元格不使用竖线，最多100行/64KiB。允许表前标题、表后普通备注；不跟随链接、不读其他笔记，也不做地理编码。缺失/打不开/格式错误/非法数值整表降级为 place=null，不报采集错误。

用球面距离匹配，定位精度圆完全落在地点半径内才认定（距离+accuracy≤半径）；多个命中取最近，同距离按名称稳定选择。无匹配 place=null；输出仅 `{place,age_minutes,sampled_at}`，不把坐标、地址或笔记原文送模型。保留旧采样年龄，不冒称实时位置；超过未来10秒的异常点不采用。

## 对话上下文

仍先通过 Binding.start：仅当前 Profile、绑定 API 根会话及合法压缩续接；其他 API 会话、reset、branch、委派、其他平台和谱系无法证明时均不请求网关/笔记。restricted 确认回合仅返回行为约束和可信确认 UUID（缺失则明确拒绝），跳过全部当天上下文及其网关/笔记读取；下一普通回合恢复读取。确认 UUID、run/session/tool 身份与 fail closed 拦截原样保留。

追加到本轮用户上下文，不改 system prompt、工具集合或历史消息。动态 JSON 预算6000 Unicode字符，连同行为约束和确认 ID 总体小于7000字符：当前上海时间/星期；已接受计划行、card_id/revision；当天 agenda 与最近 inbox/review 的 report_id、版本、可写性、已有计划卡 ID；有原值且明确允许引用的事项 id/title/list/due/completed，事件 start/end；最新位置地点与年龄/时间。v1 focus_confirmed 保留为报告所属日期的背景。

优先保留时间、位置、计划身份和报告锚点，再加入计划行和事项。逐条删减，不切断 JSON/ID/原值；超额在 omitted/truncation 明示。无完整事项时先 get_gtd_reports，不能从截断摘要造写入。最近20份之外的 inbox/review 未查会明确说明。已接受 card_id 与报告中最新 plan 卡 ID 分别提供；G1 没有“最新对话替换链节点”读取接口，不能把旧 accepted card_id 当成所有对话的最新卡；当前对话已知替换信息仍需沿历史核对。

## G4b 完成操作与报告入箱

已按补齐后的 Ollo 合同重新导出 schema，增加独立入口 OlloReportCardsInput。报告原文中的写入卡可省略 anchor，网关入箱事务补本报告真实 ID，再按 OlloCards 与生成时引用集/originals 校验；返回的 Report.cards 每张都有 anchor，raw_body 不变。对话消息仍用 OlloCards，写入卡必须显式引用上下文报告 ID，不能将报告输入规则用于对话。

complete 只有 `{op:"complete",item,by}`，只允许报告原值为未完成提醒事项。bridge 在确认组校验时拒绝事件、已完成或缺失原值，尚未 claim；backend 和 helper 对这类非法许可也不写入。helper 执行前重新核对整组：当前已完成、删除或原值变化则整组 skipped。通过后设置 `reminder.isCompleted = true` 并 `save(commit:false)`，与组内其他改动按顺序暂存，由原有流程一次 commit。循环提醒仍沿用既有限制，不扩展支持。

诊断白名单接受 op=complete，fields 为空（此操作没有可修改的载荷字段）；不记录事项 ID/标题/正文。暂存失败保持 not_executed，commit 失败保持 unknown；真实 done/skipped 回执持久保存并用于重放，不另发写入。换清单组合仍沿用既有 AppleScript move 和 unknown 规则，不宣称外部移动可回滚。

候选中「交好了」「已经还了」使用单一 write + complete；已完成项只展示，事件不提供完成按钮。首次收尾报告直接给 confirm 并省略 anchor，不再退为缺锚点的 say 卡。示例注释标明报告输入或对话 schema，安装时不把维护注释当作模型输出。

## 安装步骤（本轮不执行）

1. 设计会话先审阅本目录三份候选、地点笔记格式和 G4b 执行语义；在沙箱外复跑 Python 全套，完成 G1 schema/语义、G2 开关和四时刻合成联调。合同补齐不替代真实端到端验收。
2. **用户另行同意部署**后，在私有备份位置保存当前插件、helper、四个采集入口、两个技能、bridge.json 和四任务待修改字段；记录版本/校验值，不把备份或真实配置入库。不读取/复制密钥到报告。
3. 在维护窗口等待在途捕获/确认/helper/cron 完成。成套部署插件、helper、`scripts/ollo_collect*.py` 四个文件；脚本复制为普通文件至所属 Profile scripts，不能从仓库软链。helper 使用经过签名验证的构建，不能仅升级 Python 留旧 collect 形状。G2 plans/cards 端点须同步可用，不能在 G1/G2 不兼容时切 prompt。
4. **用户同意地点内容与路径**后，由设计会话写那一篇知识库笔记及 `knowledge.places`；可暂省略配置，place=null。不修改其他知识库内容。
5. 将 gtd-skill-v2-candidate.md、hermes-plus-gtd-v2-candidate.md 分别安装为两个技能的 SKILL.md（部署时可去除“候选/未安装/安装说明”维护句，业务/协议规则保留），四任务仅替换本候选指定 prompt，不改 ID、调度、投递、模型或 enabled_toolsets=[]。不要同时装两套互相冲突的旧候选。
6. **用户同意一次重启**后按现行部署流程重启 Gateway 并在新会话加载技能；不通过修改已有 system prompt 破坏缓存。验证健康与真实绑定、采集端点和任务结果，不能只看进程健康。
7. 先用测试事项验收：外部/订阅日历只读；今天完成与跨清单计划引用；计划404/503区别；地点命中/旧采样/null；generated gate 不调用模型；v1 agenda拒绝、v2正确锚点通过、错误锚点拒绝；报告省略 anchor 入箱补齐、对话写入缺 anchor 拒绝；complete 单项及同组修改、已完成/事件/原值变化拒绝、真实回执与重放；卡片替换和首次收尾确认。真实 Apple 写入需用户同意测试内容；自检不等于真实权限/iCloud/App验收。

## 回滚

在无在途写入的窗口，用上述私有备份同时恢复插件/helper/四脚本/两个技能/四任务 prompt/bridge.json，按已获授权的维护流程重启并验证 v1。G2/G3 的 features.cards/plans 开关由设计会话协调回退；不要让旧 schema bridge 接收新字段后误报成功。保留网关已存报告、计划、幂等回执和真实 Apple 写入，不删除数据，不把 unknown 当作可重试。回滚程序不撤销已发生 Apple 修改。

## 验证命令

用户指定的标准命令（沙箱外原样补跑）：

```bash
bash -c 'export HERMES_RUNTIME_DIR=$HOME/.cache/hermes-agent-dev/tools HERMES_HOME=$HOME/.cache/hermes-agent-dev/home; source ./activate; scripts/run_tests.sh -j 2 hermes-plugins/ --file-retries 0'
```

runner 会探测 `$HOME/.hermes/pytest_live_guard.py`；若设计会话同样只有指定线上文件读取权限，先取得运行标准测试所需的权限或用经审阅的临时 runner 副本去掉这段 guard 加载，保留其他隔离机制。不可用裸 pytest 绕过隔离。

导出当前 Ollo 合同并验证候选示例：

```bash
python hermes-plugins/ollo-bridge/contracts/export_schema.py /Users/birderyu/Projects/ollo/contracts
scripts/run_tests.sh -j 2 hermes-plugins/ollo-bridge/test_gtd_v2.py hermes-plugins/ollo-bridge/test_completion.py hermes-plugins/ollo-bridge/test_apple_diagnostic.py --file-retries 0
```

本轮 helper 构建只落开发缓存（不部署）：

```bash
BUILD_DIR="$HOME/.cache/hermes-agent-dev/gtd-v2"
mkdir -p "$BUILD_DIR/modules"
/usr/bin/xcrun swiftc -O -assert-config Debug -module-cache-path "$BUILD_DIR/modules" \
  hermes-plugins/ollo-bridge/native/EventKitBridge.swift \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
  -Xlinker hermes-plugins/ollo-bridge/native/Info.plist -o "$BUILD_DIR/ollo-eventkit"
/usr/bin/codesign --force --sign - "$BUILD_DIR/ollo-eventkit"
/usr/bin/codesign --verify --strict "$BUILD_DIR/ollo-eventkit"
"$BUILD_DIR/ollo-eventkit" --self-test
```

只运行 `--self-test`，普通执行/`--authorize` 会访问 Apple 或申请权限，不在本轮授权内。
