# 现行 gtd Skill 的差异候选（D12 / D13，未安装）

只列对设计会话只读快照 `gtd-SKILL.md` 的替换/补充点，行号针对该快照；未复制原文全文，也未修改运行 Skill。完整提示词见 [四任务候选](cron-prompts-candidate.md)，结构化合同见 [hermes-plus-gtd v1 候选](gtd-skill-candidate.md)。安装时保留 `gtd` 名称与业务规则；用户已决定停用旧消息投递。

授权分两类：**定时报告（今日安排、清理收件箱、每周回顾）提出的建议**，只能经 Ollo 卡片确认后由 `execute_gtd_confirmation` 执行，普通文字“确认”不授权这类修改；**用户在 Ollo 对话里直接提出的修改请求**，保留现行流程：先列出确切变更，得到用户在对话中的明确确认后，用 `apple-reminders` / `apple-calendar` Skill 执行。不能把报告建议改叫“直接请求”来绕过卡片确认，也不能把卡片限制扩大到所有对话写入。

| 原行 | 修改 |
| --- | --- |
| 14：读写工具职责 | 保留用户直接请求的 Apple Skill 读写流程。定时报告另由 bridge 固定只读脚本提供一次 items/originals/collected_at，报告模型不带工具；读取已生成报告用 `get_gtd_reports`。仅报告建议的修改要求卡片授权后的 `execute_gtd_confirmation(confirmation_id)`，不能绕行 Apple Skill；用户直接请求则在列出确切变更并取得对话明确确认后，仍用 `apple-reminders` / `apple-calendar` 执行。 |
| 18–28：提醒事项过滤与命令示例 | 保留所有读取先过滤再统计/展示/分类的不变量。定时报告使用 bridge 的 `source=reminder` 且 `completed === false`，单次采集后复用，不让报告模型执行旧命令；对话内 Apple Skill 读取仍遵守其 `isCompleted == false` 过滤规则。禁止用原始数组长度推断未完成数，不把失败当零项。 |
| 32–36 后：六清单、三日历 | 补充这些均为逻辑名，`bridge.json` 的 `apple.lists`/`apple.calendars` 可对应测试名称，缺省同名且各自一一对应。采集、卡片、originals 和确认比较共用逻辑名，不据测试名称再改提示词或建议目标。 |
| 40：清单归属为准 | 使用 bridge 数据时，真实清单归属由 helper 映射后的逻辑 list 表示；对话内 Apple Skill 仍按实际清单归属判断。保留不按标题前缀判状态、不自动移动的规则。 |
| 66：超期处理选项 | 保留业务选项。仅对报告建议的 v1 卡片执行，完成/清空日期尚未支持，只能建议，不能编造 change 或绕行通用工具；null 不等于清空。这不限制用户直接请求并在对话中明确确认后的 Apple Skill 操作。 |
| 78–80：授权、Photon/iMessage 专属措辞 | 保留先列出确切变更、取得明确确认、仅授权所列变更的规则；删除仅针对旧平台的“request a plain-text reply such as 确认 or 取消; do not use a native poll”。在 Ollo 中区分：报告建议必须卡片确认，普通文字“确认”不授权这类修改；用户直接请求仍接受对话明确确认，随后用 `apple-reminders` / `apple-calendar` Skill 执行。卡片路径的工具只接收当前 confirmation_id，不按最近确认猜测，不传 changes/originals/run_id；核对可信 run 与 UUID，逐组领取许可、比较 originals，以持久回执说明结果，unknown 不换 UUID 重试。 |
| 84：日历交叉核对 | 保留预约不等于完成与跨源核对规则；定时任务复用一次采集，范围外明确未查。inbox 只采收件箱，不能声称已查其他清单/日历。 |
| 88：定时投递 | 将“delivered through Photon/iMessage”改为经 Ollo 投递；四个既有任务单投 `ollo:main`，由网关保存报告、呈现卡片与推送。报告只启动/提出工作流，不授权修改；定时任务不调用发送工具、不保留双重投递。 |
| 95、102、108 后：报告输出 | 增加 v1 围栏、计数、引用与同次 originals 规则。保留工作日 1–3 / 周末 1–2 / 每周不超过 3 个重点、周末生活家庭优先、0 项也发、不称新增、允许延后、全部超期及不自动修改；失败不输出虚假空卡片。 |
| 110：周回顾采集实现 | 改为 `script=ollo_collect_review.py` 一次调用 collect，六清单全部未完成及采集时刻起未来七天三日历。agenda 用 `ollo_collect_agenda.py`（当日日历），inbox 用 `ollo_collect_inbox.py`（仅收件箱）。每源只读一次并复用，500 项硬上限；超限/超时/权限不足明确失败。模型 `enabled_toolsets=[]`，不执行采集命令、不自行重试。 |

另补卡片确认结果输出：仅报告建议的卡片确认回合执行 `execute_gtd_confirmation` 后附一个 `hermes-plus-gtd-result` v1 围栏，内容取持久回执；confirmation_id 只作关联，不加合同未声明字段。报告建议 status=handled 不等于已写入。用户直接请求的 Apple Skill 操作按实际执行结果回复，不伪造卡片确认 ID 或该回执围栏。

其余业务规则不改：信用卡提醒偏移、长期/等待/责任清单、多步事项只留当前下一步、无日期重点候选、全部超期、预约不等于完成、四个时刻与名称。完整原 Skill 不随本仓库分发。

部署核对：当前宿主把 pre-run 失败交给模型，不自动设置失败投递；采集窗口取实际采集时间，非原 wake 日期。具体限制见 [宿主边界](cron-prompts-candidate.md#宿主边界与上线核对)，Skill 不承诺尚未接通的失败兜底或跨日一致性。
