运行校验使用 Ollo bridge 0.2.0-draft 合同的离线 schema 闭包；B_ 为 bridge、P_ 为公共模型。不运行时读取其他仓库，也不远程解析引用。

`export_schema.py` 导出全部 bridge 模型，加上插件直接调用的公共入口 `GTDResult`、`GTDSuggestion`，递归收齐依赖并将引用改为本地 `$defs`。当前含 A2 capture lookup、A4 delivery_kind、A5 GTD 标题/originals 4096 UTF-8 字节限制与 A6 日期规则，共 101 个定义。仅从 bridge 的 `$ref` 出发会漏掉确认结果的 `P_GTDResult`；它是 Python 直接校验的入口，不被 bridge 模型引用。

在已激活的 Hermes 开发环境中，从仓库根目录重新生成（参数为 Ollo 源合同目录）：

```sh
python hermes-plugins/ollo-bridge/contracts/export_schema.py /path/to/ollo/contracts
```

脚本使用宿主已有的 `ruamel.yaml`，只供维护时导出，不由插件运行时加载。`test_apple_contract.py` 检查整包及直接使用的公共入口，断言所有 `$ref` 均为本地且可解析；确认执行测试继续校验真实结果结构。

A5 的全部 GTD 标题类字段（事项、重复/冲突提示、修改/合并/新建、建议、候选标题、agenda、originals）同时受 `maxLength: 4096` 与 `x-max-utf8-bytes: 4096` 约束；采集原值不截断。wire 使用紧凑 UTF-8 JSON，items 响应上限 2097152 字节，超限为 `413/payload_too_large/false`。HTTP 预算与完整错误映射由插件代码落实，不能只靠导出的字段 schema；对应测试见 `test_http_contract.py`、`test_text_contract.py`。

字段 x-max-utf8-bytes 超限也为 413，包含组合 schema 的内层校验错误；字符数、类型、必填和其他结构错误保持 400。`test_schema_errors.py` 覆盖这一区分及合法替代分支；模型输出错误由 interpret 再统一为 invalid_model_output。N3 不改变合同/schema 的内容或字段限额。

A6 的 ISODate / ISODateTime 使用完整 pattern，校验真实日期、时间范围与必填时区；GTDDate 的 oneOf 在没有任何格式检查器时也互斥。ISODateTime 允许分钟、秒和小数秒精度，移除要求必填秒的 `format: date-time`。Hermes 不新增 `rfc3339-validator` 等依赖；`test_datetime_contract.py` 显式关闭 date-time 或所有格式检查，覆盖整包和派生 schema 的报告、建议、确认、ScheduleItem，以及 Python 分钟精度解析。EventKit helper 仅对分钟精度输入补秒，既有输出格式保持不变；其 `--self-test` 由 macOS 原生合同测试调用。

源文件 SHA-256（2026-10-12，A6）：

- `bridge.openapi.yaml`: `6f3c724bdaa4ad1338d9345c37b41adab1378fa424de21d69ac06a6d69a23330`
- `openapi.yaml`: `7b14e63f1c93ba5fee338a9f9047552246cacb550a32a327e81fe0b54d8f2931`

fixtures 保留 2026-10-11 的 42 份合成合同样例，含 failure 投递/保存报告/回执，不是用户数据；A6 日期边界在新测试中基于这些样例构造。本轮完整重导出也同步了源合同关于 agenda 成功日账与展示正文去围栏的说明，网关行为不在插件里另行实现。合同更新时重新导出并审阅语义，禁止只更新散列。
