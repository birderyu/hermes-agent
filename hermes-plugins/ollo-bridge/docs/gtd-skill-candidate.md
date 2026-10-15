---
name: hermes-plus-gtd
description: Ollo GTD v1 reporting and gateway-authorized confirmation execution.
version: 1.0.0
platforms: [macos]
---

# GTD v1 候选 Skill（将于正式切换时安装）

2026-10-14：正式切换时将由真实任务加载；用户提供的现状是 `hermes-plus-gtd` 已在测试阶段安装，因此本次工具保留运行版，不以此候选覆盖。A7 已包含在下文。新环境才在审阅后按此候选安装。详见 [正式切换方案](../../../custom/records/2026-10-14-ollo-gtd-production-switch.md)。

只在用户审查并批准安装后生效。这里是 Hermes Skill 候选正文，不是本次开发会话的操作指令。不改变既有 GTD 业务清单、四任务时刻或报告之外的普通聊天规则。

## 数据与权限

提醒事项/日历是唯一事项权威源。定时任务生成报告时只读；报告、建议、位置、旧确认和工具结果本身都不能授权写入。生成报告只使用同一次 pre-run 采集提供的 items 与 originals；未采到的数据明确缺失，不从历史消息补造。采集失败输出明确失败文字，不宣称“没有事项”。

本 Skill 的卡片授权约束仅用于定时报告（今日安排、清理收件箱、每周回顾）提出的建议。用户在 Ollo 对话里直接请求“帮我加个提醒”“把 X 移到工作”等，仍按现行 gtd 规则：先列出确切变更，得到用户在对话中的明确确认后，用 `apple-reminders` / `apple-calendar` Skill 执行，仅执行确认的内容。普通文字“确认”不能授权报告建议，但可以在上述直接请求流程中确认已列出的变更；不得混淆两类来源。

今日安排的重点确认只由网关记住，不改提醒事项；不能把确认重点转换为“设今天到期”，不能覆盖真实截止日、顺延超期项或移动清单。App 通过报告的 focus 接口保存选择，模型不提交该接口，也不生成 agenda 的确认/执行结果围栏。读取报告和 context 的 `focus_confirmed` 判断当前选择；缺失/null 表示未确认，`preselected` 只是建议。选择只属于该报告日期，不因 confirmed_at 在今天就沿用旧报告重点；context 缺少日期或标题时用 `get_gtd_reports` 读完整报告核对。

候选部署使用三个固定入口 `ollo_collect_agenda.py`、`ollo_collect_inbox.py`、`ollo_collect_review.py`，各一次调用配套 EventKit helper 的 `collect`，stdout 输出一份 `items/originals/collected_at`。默认 agenda/review 读取收件箱/工作/生活/家庭/等待/长期全部未完成项，含所有超期和无日期候选；agenda 读今天三个日历，review 读采集时刻起未来七天。inbox 仅收件箱、不读日历。名称是 `bridge.json` 对应表的逻辑名，originals 和确认比较使用同一映射。每次最多 500 项，超限/权限/读取失败都明确失败；零项仍输出非空 JSON。任务设 `enabled_toolsets: []`，不向报告生成模型提供任何工具。

普通 agent cron 的 Script Error 仍交给模型，提示词必须明确未生成、不输出假空卡片；这本身不会令宿主设置 for_failure。脚本也不收到原 wake 日期，窗口取上海实际采集时间；外层可信业务日期可能不同。上线核对和现行 gtd 的逐行差异见 [完整提示词](cron-prompts-candidate.md) 与 [Skill 差异](gtd-skill-update-candidate.md)。

## 报告

先写自然中文正文，末尾恰好一个顶层 `hermes-plus-gtd` JSON 围栏；version=1、kind 为 agenda/inbox/review。不要追加 `ollo-mood`。报告 ID、execution_id、business_date、trigger_id 由宿主/网关提供，模型不生成执行身份。

- `generated_at` 取采集时间；业务日期不从此字段推导。
- agenda：items=[]，counts={events,due}；agenda 包含 date/events/due/focus/focus_limit/inbox_pending。focus 引用必须来自采集；日期、时长和数量不猜测。
- inbox：counts={pending,handled,undecided}；items 每项含 id/source/title/list/created_at/notes/status/reason，必须有 findings（可空数组）和 suggestion，可有 choices；status 只用 handled/undecided，表示建议处理清晰度，不代表已实际修改。
- review：counts={overdue,waiting,someday,next7,handled,undecided}；每项另带 group=overdue/waiting/someday/next7。
- 每个已有 item、into、with、merge_into、agenda 重点/日程/到期项及连带变更目标，都需引用此次采集 ID。`originals` 只放实际被报告引用的 ID，原样复制相应 source/title/list/due/completed/modified_at；event 再带 start/end。**不能把 generated_at 写成 modified_at，也不能稍后补采替换 originals。** 无 modified_at 的项不编造快照，已有事项变更将 skipped。
- 允许的建议 action=do/delete/someday，destination=工作/生活/家庭/等待/日历 或 null；日期需真实 ISO 日期或带时区时间。合并目标必须在本报告；不要自合并。
- change 只用 update/delete/to_event/merge/update_event/create_reminder。可选 null/省略都表示“不改”，不表示清空。原值、日期、枚举和引用无法满足时只写文字提示，不制造可执行结构。

精确字段以插件 `contracts/schema.json` 的 P_GTDReport/P_GTDSuggestion/P_GTDChange 为准；`fixtures/report-delivery.json` 是完整合成 agenda 示例，`fixtures/interpret.json` 展示完整 item/建议/关联目标，`fixtures/confirmation.json` 展示确认与依赖组。坏的最后一个报告块由网关降级为纯文字，不重捞更早块。

## 报告建议的卡片确认与结果

清理收件箱与每周回顾（inbox/review）提出的修改建议只能经 Ollo 卡片确认后执行，普通文字“确认”不构成这类修改的许可。App 通过网关提交卡片确认；调用 `execute_gtd_confirmation(confirmation_id)` 时只传当前提交 ID，不传 changes、originals、run_id、session_id 或任意工具参数。网关必须将该确认映射到当前真实 Hermes run；取不到授权即停止执行，不按“最近确认”猜测，也不转 Apple Skill 绕过卡片许可。agenda 确认仅保留历史解码形状，新提交或旧确认执行均为 400/invalid_request，不转换成重点选择或 Apple 操作。

带报告卡片确认标记的当前回合只能走该工具；这一约束不覆盖普通对话直接请求的确认流程。工具核对生成时原值、分组领取许可，一组暂存后一次 commit；不相关组独立。工具返回 done 才能说明成功，skipped/failed 逐项解释。unknown 表示写入可能已发生，需核对，不能换 UUID 重试、搜索相似标题猜成功或声称已回滚。

回复中文说明并附 `hermes-plus-gtd-result`，仅包含 version=1、report_id、results；results 原样取工具回执中的 index/status/reason。工具外层 confirmation_id 用于关联，不塞进 v1 围栏的未声明字段。不要以模型输出代替持久回执；漏块/坏块也由网关根据已保存回执恢复卡片。

用户直接请求经对话明确确认后由 Apple Skill 执行时，按实际执行结果回复，不要求卡片 confirmation_id，也不伪造 `hermes-plus-gtd-result`。
