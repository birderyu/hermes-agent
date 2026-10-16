# 离线合同导出

2026-10-16 G4：本轮从 Ollo 工作区未提交的 G1 合同重新导出 schema.json，新增显式入口 OlloCards/DayPlan 及所有传递引用。运行时只读此本地闭包，不访问 Ollo 仓库。导出器原被根目录 `export*` 忽略，已在本目录用精确例外纳入可审阅文件。

G4b 已从补齐后的合同重新导出，当前支持 GTDReport v2、OlloCards、OlloReportCardsInput、PlanRow、DayPlan、BridgeLatestLocation 及 complete。9 个候选卡片示例用标明的输入 schema 离线校验通过，其中 6 个报告示例省略 anchor；补上合成报告 ID 后也通过 OlloCards。`test_gtd_v2.py` 会再次验证三份候选的全部 ollo-card JSON。schema 校验不证明锚点真实存在/引用可写/替换关系/实际执行；G5须补语义和端到端验收。

complete 执行形状为 op/item/by，模板中的 item 可用 DraftRef。源为 event 或生成时已完成/缺失原值属于业务拒绝，schema 合法本身不能授予执行。确认组、backend 与 helper 均检查该边界；完成和其他改动复用原有依赖组许可、单次 commit 和真实回执。

报告原文使用 OlloReportCardsInput，省略的 anchor 由网关入箱事务填本报告 ID，再按 OlloCards 和 items/originals 校验；消息写入卡仍按 OlloCards，必须显式 anchor。两种入口不能互换，省略不等于 null/self/占位符。`fixtures/gtd-v2/` 同步 6 份新的合成合同样例，`test_completion.py` 覆盖输入区别及完成操作。

v2 agenda 执行会先读锚定报告，必须 kind=agenda 且 version=2；v1仍拒绝。部署须同步 G1/G2 支持的端点、插件/helper/技能/prompt，不能沿用下面A7“先bridge后网关”历史说明直接切v2采集。

运行校验使用 Ollo bridge 0.2.0-draft 合同的离线 schema 闭包；B_ 为 bridge、P_ 为公共模型。不运行时读取其他仓库，也不远程解析引用。

`export_schema.py` 导出全部 bridge 模型，加上插件直接调用或候选示例校验的公共入口 `GTDResult`、`GTDSuggestion`、`OlloCards`、`OlloReportCardsInput`、`DayPlan`，递归收齐依赖并将引用改为本地 `$defs`。当前共 121 个定义。仅从 bridge 的 `$ref` 出发会漏掉 `P_GTDResult` 与纯报告原文输入等入口；这些需要显式加入导出根。

在已激活的 Hermes 开发环境中，从仓库根目录重新生成（参数为 Ollo 源合同目录）：

```sh
python hermes-plugins/ollo-bridge/contracts/export_schema.py /path/to/ollo/contracts
```

脚本使用宿主已有的 `ruamel.yaml`，只供维护时导出，不由插件运行时加载。`test_apple_contract.py` 检查整包及直接使用的公共入口，断言所有 `$ref` 均为本地且可解析；确认执行测试继续校验真实结果结构。

A5 的全部 GTD 标题类字段（事项、重复/冲突提示、修改/合并/新建、建议、候选标题、agenda、originals）同时受 `maxLength: 4096` 与 `x-max-utf8-bytes: 4096` 约束；采集原值不截断。wire 使用紧凑 UTF-8 JSON，items 响应上限 2097152 字节，超限为 `413/payload_too_large/false`。HTTP 预算与完整错误映射由插件代码落实，不能只靠导出的字段 schema；对应测试见 `test_http_contract.py`、`test_text_contract.py`。

字段 x-max-utf8-bytes 超限也为 413，包含组合 schema 的内层校验错误；字符数、类型、必填和其他结构错误保持 400。`test_schema_errors.py` 覆盖这一区分及合法替代分支；模型输出错误由 interpret 再统一为 invalid_model_output。N3 不改变合同/schema 的内容或字段限额。

A6 的 ISODate / ISODateTime 使用完整 pattern，校验真实日期、时间范围与必填时区；GTDDate 的 oneOf 在没有任何格式检查器时也互斥。ISODateTime 允许分钟、秒和小数秒精度，移除要求必填秒的 `format: date-time`。Hermes 不新增 `rfc3339-validator` 等依赖；`test_datetime_contract.py` 显式关闭 date-time 或所有格式检查，覆盖整包和派生 schema 的报告、建议、确认、ScheduleItem，以及 Python 分钟精度解析。EventKit helper 仅对分钟精度输入补秒，既有输出格式保持不变；其 `--self-test` 由 macOS 原生合同测试调用。

A7 在公共 Report、BridgeReport/Page 的 report 和 BridgeContextReport 中支持可选的 `focus_confirmed: {item_ids, confirmed_at} | null`；confirmed_at 为 Unix 秒。缺失/null 均表示未确认；不从不可变 raw_body 的 preselected 推断选择，也不将确认时刻当报告日期。工具额外返回的 `focus_summary` 只是给模型的简短注释，保留完整报告原样，不是网关 HTTP 合同字段；context 无独立报告日期，摘要只注明所属报告，需要时读完整报告核对。`test_focus.py` 覆盖字段读回、派生 schema、摘要与历史日期边界。

GTDConfirmation 仍保留 agenda 供旧历史解码；新写入/旧确认领取许可已不支持它。bridge 在可信确认读回后、领取任何组之前拒绝 agenda，返回 `400/invalid_request/false`；网关返回同样错误时也原样分类，不改成可重试失败，不转成 Apple 写入或 focus PUT。inbox/review 保持原有确认执行和回执语义。

**部署顺序：先升级 bridge，再升级网关。** A6 及更早 bridge 的 Report/BridgeContextReport 使用 additionalProperties=false，会拒绝新增字段，连 null 也拒绝。A7 网关序列化会始终带该字段：旧 bridge 的报告读取工具报 invalid_request，context 降级为摘要暂不可用，items/interpret 的报告预读取也会被阻断。新 bridge 接受旧网关省略字段；本项不修改报告投递回执结构。切换仍应等待旧 agenda 在途 run 结束；已签发许可的 finish 保留真实回执，不删除或回滚既有写入。

源文件 SHA-256（2026-10-13，A7）：

- `bridge.openapi.yaml`: `9b9d288542c3a7d252f6a8e22c86d3a3981433f700a3f1c880b7f9e98b72b7f6`
- `openapi.yaml`: `b5e1d070cf95268507b0bbbf8f91a1170c9ea4822694236aa4f8e246d1dff5e6`

fixtures 保留 2026-10-11 的 42 份合成合同样例，并同步 A7 的 `report-focus-confirmed`、`context-focus-confirmed` 两份，共 44 份，不是用户数据；A6 日期和 A7 未确认/非法选择边界由测试构造。合同更新时重新导出并审阅语义，禁止只更新散列。

G1 源文件 SHA-256（本轮读取的未提交工作区快照）：

- `bridge.openapi.yaml`: `a4f663b731e7ffdd77411670c6a0f2054c5b6aa7a78f1a8fc440c3d995870e14`
- `openapi.yaml`: `a7230394c29b3e093e90fae89fe72e890653884ba85e3b895a7386f8c749154a`

G4b 源文件 SHA-256（2026-10-16，当前 schema.json 对应快照）：

- `bridge.openapi.yaml`: `b7f6c26d18888a2046d282f5e14f10ece76791d8ea0259b019b56dc350484fe7`
- `openapi.yaml`: `d88f6e09ee6442844d992c00467d117171ad51d148a95768d219c98cb67a95c5`
