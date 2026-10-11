运行校验使用 Ollo bridge 0.2.0-draft 合同的离线 schema 闭包；B_ 为 bridge、P_ 为公共模型。不运行时读取其他仓库，也不远程解析引用。

`export_schema.py` 导出全部 bridge 模型，加上插件直接调用的公共入口 `GTDResult`、`GTDSuggestion`，递归收齐依赖并将引用改为本地 `$defs`。当前含 A2 capture lookup、A4 delivery_kind 与 A5 GTD 标题/originals 4096 UTF-8 字节限制，共 101 个定义。仅从 bridge 的 `$ref` 出发会漏掉确认结果的 `P_GTDResult`；它是 Python 直接校验的入口，不被 bridge 模型引用。

在已激活的 Hermes 开发环境中，从仓库根目录重新生成（参数为 Ollo 源合同目录）：

```sh
python hermes-plugins/ollo-bridge/contracts/export_schema.py /path/to/ollo/contracts
```

脚本使用宿主已有的 `ruamel.yaml`，只供维护时导出，不由插件运行时加载。`test_apple_contract.py` 检查整包及直接使用的公共入口，断言所有 `$ref` 均为本地且可解析；确认执行测试继续校验真实结果结构。

A5 的全部 GTD 标题类字段（事项、重复/冲突提示、修改/合并/新建、建议、候选标题、agenda、originals）同时受 `maxLength: 4096` 与 `x-max-utf8-bytes: 4096` 约束；采集原值不截断。wire 使用紧凑 UTF-8 JSON，items 响应上限 2097152 字节，超限为 `413/payload_too_large/false`。HTTP 预算与完整错误映射由插件代码落实，不能只靠导出的字段 schema；对应测试见 `test_http_contract.py`、`test_text_contract.py`。

字段 x-max-utf8-bytes 超限也为 413，包含组合 schema 的内层校验错误；字符数、类型、必填和其他结构错误保持 400。`test_schema_errors.py` 覆盖这一区分及合法替代分支；模型输出错误由 interpret 再统一为 invalid_model_output。N3 不改变合同/schema 的内容或字段限额。

源文件 SHA-256（2026-10-11）：

- `bridge.openapi.yaml`: `957dfd6b5d7c31463bacddecfda526c5e2a2a6b26e0db876f0fc544d102ed55a`
- `openapi.yaml`: `919793cc21fa475e96e9802de78ad5a9dd9867df98b071f52db045e5533f2f90`

fixtures 为该时刻的 42 份合成合同样例，含 failure 投递/保存报告/回执三份新增样例，不是用户数据。合同更新时一并重新导出并审阅语义，禁止只更新散列。
