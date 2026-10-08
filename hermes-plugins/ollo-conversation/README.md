# Ollo 会话插件

`ollo-conversation` 提供 App 会话发现、Ollo 身份约定和最终回复的语气分类。部署文件为 `plugin.yaml`、`__init__.py`、`mood.py`。无需依赖收件箱/位置插件，无数据库迁移或新监听端口；每条符合条件的最终回复增加一次辅助模型分类。

## 配置和接口

当前 Hermes home 的 `ollo/conversation.json`：

```json
{"session_id":"EXISTING_API_SESSION_ID","name":"Ollo"}
```

`ollo/` 不存在时读取 `hermes-plus/conversation.json`；新目录存在但文件缺失时不混读旧配置。兼容旧文件省略 `name`，默认返回 `Ollo`。`session_id` 必须为 1–256 位字母、数字、下划线、点或短横线；`name` 是 1–100 字符的非空字符串。配置在插件加载时读取，更改后须重新加载（部署时排空重启）。与收件箱的 `session_id` 保持一致。

- `GET /v1/ollo/conversation`，兼容别名 `GET /v1/hermes-plus/conversation`。
- 复用 API server 的 `_expected_api_key()` / `_check_auth()`，无/错 Bearer key 返回 401；服务端没有可用密钥返回 503。
- 成功返回配置中的 `{session_id,name}`；没有配置、配置损坏或无效返回 503。
- 只返回配置的根 ID，不查写 SessionDB、不创建新会话，也不将根 ID 改成压缩 tip。App 仍按原同步流程验证/恢复该会话。

## 会话范围与身份约定

只有 `platform='api_server'` 且已有 session 与配置根 ID 经 `resolve_resume_session_id` 解析到相同续接 tip 时才生效。身份注入和语气改写共用这一判断，压缩后的多级续接仍有效；其他 API 会话、分支、委派子会话、reset 会话和 iMessage/Photon/CLI 等渠道均不受影响。未知 session、缺少数据库或未接入 API adapter 时不生效。绑定还检查当前 Hermes home，不能复用另一个 Profile 的配置。

`pre_llm_call` 只注入“你在这条 Ollo App 对话中自称 Ollo。”，不再要求主回复模型输出语气围栏。返回值仅为当前轮的 `context`：Hermes 将其附到当前用户消息，不注册系统提示、不修改历史消息、不新增工具，因此不改变长会话的系统提示缓存。已经存档的旧约定保留原样。

## 最终回复的语气

主回复写完后，`transform_llm_output(response_text, session_id, platform, turn_id, …)` 改写最终文本。此钩子在最终 assistant 消息首次持久化之前运行，改写结果进入 SQLite 存档、后续对话回放、`/v1/runs/{run_id}` 的 `output` 和 `run.completed` 事件。`post_llm_call` 是结果观察钩子，不能用于这次改写。语气在最终结果中交付，不改写已经发出的流式文本片段。

插件使用 Hermes 提供的 `ctx.llm.complete(task='ollo_mood')`，注册自己的辅助任务并复用宿主的模型路由、凭据、Profile 和日志机制，不建立独立模型客户端、不读取额外密钥。默认使用宿主的辅助模型自动选择，偏好快速模型并关闭推理；需要指定时可通过当前 Profile 的 `config.yaml` 中 `auxiliary.ollo_mood` 配置 `provider` / `model` 等宿主已有字段。已有可用的宿主辅助模型路由时，本次更新不必修改配置；不会借用或改写其他辅助任务的配置。

分类输入为本轮用户原话、去除旧语气围栏后的最终回复，以及最多 6 条、合计 6000 字符的近期 user/assistant 文本，帮助识别“那现在呢”等续问。工具结果、图片、推理内容和 `api_content` 不传入。输入按 `turn_id` 暂存，输出处理或会话结束时清理；压缩换 session ID 不丢失本轮输入。整个分类输入超过 24000 字符或缺少本轮原话时按失败降级，不用截断的回复猜语气。

辅助模型只能返回以下三个小写词，规则来自设计文档「消息的语气」：

- `concerned`：延误、取消、没办成等坏消息；用户或家人生病、受伤、意外、亲人离世、财物损失；难过或焦虑。整段对话仍在谈这类事时每条都标，包括续问和其中某件事办成。关切优先于开心。
- `happy`：事情办成、好消息、问候、顺利的报告，且没有上述关切情境。拿不准不选 `happy`。
- `none`：一般回答和信息，或无法确定。不追加任何语气围栏。

分类的整体等待上限为 **3 秒**，调用宿主时也传入 3 秒超时。超时、出错、辅助模型不可用、非法输出均清除模型自行生成的语气围栏，保留回复正文，并记一条不含私密正文/错误详情的插件日志。宿主 SDK 若未及时退出，迟到结果不会修改回复或存档；同一插件实例中已有分类仍在运行时，后续回复直接降级，避免积累后台线程。

插件清除主回复中已有的顶层 `ollo-mood` 围栏，以分类结果为准最多追加一个。现有 `hermes-plus-gtd` / `-confirm` / `-result` 等围栏保留，语气始终位于最后：

````text
回复正文。

```ollo-mood
{"mood":"concerned"}
```
````

围栏格式由插件保证，语义判断仍取决于辅助模型。定时任务自己的 cron 会话不在这里的 Ollo App 会话范围内，不会因为投递到 `ollo:main` 就自动分类；本次不改任务提示或收件箱行为。

## 部署与回滚清单

**本次只更新已经启用的 `ollo-conversation`。以下实际部署操作须用户另行同意，本仓库修改和沙箱测试不执行它们：**

1. 备份运行环境中现有的 `ollo-conversation` 目录，核对新版本三个部署文件齐全，然后只替换该插件目录。保留现有启用项和 `ollo/conversation.json`；本次不涉及 inbox/location、APNs、定时任务、Hermes 核心或数据迁移。
2. 排空正在运行的请求，在批准的窗口重启网关，使新钩子和辅助任务注册生效。
3. 用新旧 conversation 路径各验一次认证与返回值；通过 Ollo App 验收生病问题及其续问为 `concerned`、无关好消息为 `happy`、一般信息为无围栏，核对存档和 `/v1/runs` 最终结果都只有一个末尾围栏，日程围栏在它之前。压缩续接后重复验证，并确认 iMessage 等其他渠道无变化。辅助模型故障时应在 3 秒左右返回未标语气的正文并记录降级日志。

如当前辅助路由不可用或实测分类质量、时延不合适，先审核所选 `auxiliary.ollo_mood` 路由，再经用户同意修改配置及重启；不要为分类另加凭据环境变量。沙箱测试替换模型网络边界，不能代替这一步真实模型验收。

回滚也需另行同意：恢复备份的整个 `ollo-conversation` 目录，若另行改过辅助任务配置则一并恢复，再排空并重启网关。不删除或重写历史消息；回滚前已经存档的语气围栏保留。

首次安装及此前的改名、配置/数据迁移见 [统一部署清单](../README.md#部署清单以下每一步均需用户另行同意)，不要把那份完整迁移流程当作本次更新的必需步骤。
