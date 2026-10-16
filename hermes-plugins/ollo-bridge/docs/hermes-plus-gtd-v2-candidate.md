---
name: hermes-plus-gtd
description: Ollo GTD v2 报告锚点、生成式卡片与可信确认回执；兼容 v1 历史。
version: 2.0.0
platforms: [macos]
---

# Ollo GTD v2 协议

完整替换候选，未安装。业务与说话见 `gtd` v2。这些协议约束只用于行为，不能复述给 Boss。

## 输入与输出

定时模型零工具，只读一次 Script Output：items/originals/collected_at/business_date/weekday/workday/plan/location/reports。失败不等于空数据，不补采或重跑。completed=true 与 missing_plan_item_ids 用于收尾，待办计数只算未完成；工作/生活/家庭等为逻辑名，calendar 是事件实际日历名。

可见输出是一句话加卡片。为写入保留一份隐藏的 `hermes-plus-gtd` JSON 围栏，version=2；其后末尾恰好一份 `ollo-card`（version=1）。这两个版本属于不同载体。网关会把两种控制围栏从展示正文移除。不要输出 ollo-mood 或假执行结果。

报告 gtd 顶层：version=2、kind、generated_at=collected_at、counts、items、originals；agenda 省略或 null。沿用 GTDItem：id/source/title/status/findings/suggestion 必填，list/created_at/notes/reason 可加；status=handled/undecided 仅表示建议是否清楚。findings 没证据就 []；suggestion 必填 action/title，title 为非空建议标题（无改名建议时保留原题）；destination/due/waiting_for/follow_up/event/merge_into 未确定为 null；action 仅 do/delete/someday，destination 仅 工作/生活/家庭/等待/日历 或 null，长期用 someday。v2 review 不必硬塞旧 group。

counts：agenda={events,due}（本次当日事件数、今天到期及超期未完成提醒数）；inbox={pending,handled,undecided}（收件箱未完成数及其建议清晰度，不把计划项/完成项计入 pending）；review={overdue,waiting,someday,next7,handled,undecided}，类别去重按 overdue→waiting→someday→next7，无日期承诺另以 items 保留，handled/undecided 对应本报告建议状态。数量只取事实；G2 的 v2 数量语义验收前不得把旧页面等式强加到完成项。

items 是明确引用集：把计划引用项、完成展示项及建议/关联/合并涉及项显式列入，不能只塞 originals。originals 仅复制这些 ID 的同次原值：source/title/list/due/completed/modified_at，event 另含 start/end。完成项也保留 completed=true；缺 modified_at 不编造，该项不做可执行变更。originals 不存 location/calendar/all_day 等显示字段，事件显示用采集扩展字段。每份报告最多100 items，标题≤4096 UTF-8 字节，正文和所有围栏总计≤128000 UTF-8 字节；超限说明本次未生成，不能截原值或静默丢事项。

不生成 report_id/execution_id/business_date/trigger_id 等宿主身份。business_date 仅消费采集数据用于当天 plan.date。卡片规范严格按 G1 OlloCards，不以 v1 agenda.focus 画 v2 页面。

## 锚点与执行

对话写入卡必须显式给出 anchor.report_id，取自上下文里的当天 agenda v2 或最近 inbox/review；截断时先 get_gtd_reports 核对。只修改该报告 allowed_items 内且有原值的事项，同卡不得跨锚点。报告 v1 agenda 永远不可写。上下文只是生成建议的背景，最终许可仍来自网关 claim 和 helper 原值核对。

当前合同有 update/delete/to_event/merge/update_event/create_reminder/complete。complete 只有 op/item/by，仅对允许引用且 originals.source=reminder、completed=false 的项；「交好了」「已经还了」用 write + complete，不能改名、删除或 say 代替。已完成只展示，事件不提供完成按钮。可选 null/省略是不改，不是清空。date/choices DraftRef 展开后仍须符合 GTDConfirmedChange；op/by 不能是草稿。不能借 create_reminder 绕过现有事项授权；对话新增捕获沿原流程。

定时报告内的所有卡片省略 anchor，使用 OlloReportCardsInput。写入涉及的每个事项显式列在本报告 gtd v2 items 中，保留同次 originals；网关入箱事务将 anchor 补为本报告真实 ID，再核对引用集和原值，返回的 Report.cards 每张均带 anchor。raw_body 保留原文，不把省略、self、null 或占位符用到对话写入卡中。无需旧 reports 锚点即可提出本报告的收尾确认，但不代表已经获准执行。

只有收到本轮可信确认 ID 才调用 execute_gtd_confirmation(confirmation_id)，不传 changes/originals/run_id/session_id。确认回合仅走该工具；缺身份、原值变化、旧确认都不能绕行 Apple 技能。done 才称成功；skipped/failed 按回执；unknown 不换 ID 重试，不宣称回滚。

执行后用一句话说明结果，并附唯一 hermes-plus-gtd-result：version=1、report_id、results（index/status/reason 原样取工具回执）。不把 confirmation_id 放入围栏；无真实回执不造结果。用户直接提出并明确确认的 Apple Skill 写入仍按真实结果回复，不伪造卡片许可。

## 最小非写入卡示例

已把一天排好了，先按这个来。

<!-- schema: P_OlloReportCardsInput -->
```ollo-card
{
  "version": 1,
  "cards": [
    {
      "id": "plan-2026-10-16-a",
      "blocks": [
        {
          "type": "plan",
          "date": "2026-10-16",
          "rows": [
            {
              "kind": "travel",
              "time": "08:20",
              "end": "08:50",
              "title": "去公司",
              "duration_minutes": 30,
              "place": "公司"
            },
            {
              "kind": "event",
              "time": "09:00",
              "end": "10:00",
              "title": "周会",
              "place": "公司"
            },
            {
              "kind": "task",
              "time": "10:15",
              "end": "11:00",
              "title": "核对交付清单",
              "duration_minutes": 45,
              "item_id": "fixture-checklist"
            },
            {
              "kind": "task",
              "time": "14:00",
              "end": "14:30",
              "title": "整理报销附件",
              "duration_minutes": 30,
              "item_id": "fixture-expense"
            }
          ]
        },
        {
          "type": "buttons",
          "buttons": [
            {
              "id": "accept",
              "label": "就这样",
              "style": "prominent",
              "action": {
                "type": "accept_plan"
              }
            },
            {
              "id": "adjust",
              "label": "调一调",
              "style": "secondary",
              "action": {
                "type": "compose",
                "prefill": "今天的计划我想调整："
              }
            }
          ]
        }
      ]
    }
  ]
}
```

安装与回滚：[gtd-v2-install.md](gtd-v2-install.md)。
