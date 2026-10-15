# Ollo GTD 换清单改用 Reminders AppleScript move

日期：2026-10-15。状态：开发修改，未提交、未推送、未部署、未重启。没有访问真实提醒事项、运行 home 或密钥。本次只编辑 `hermes-plugins/ollo-bridge/`、本记录及记录索引；`2026-10-15-v0.22.1.md` 的会话已有改动原样保留。Ollo 仓库只读。

## 问题与证据来源

以下为用户提供的 2026-10-15 设计会话实测事实，**本次没有在真实数据库重做实验**：

- 10-14 切到真实清单后，4 次含 `list` 的 update 都在 commit 失败；诊断为 `domain=com.apple.reminderkit / code=-3002 / phase=commit / op=update / fields=[due,list]`。仅日期 update 和 delete 成功，失败事项原样未变。
- `reminder.calendar = 其他清单` 在 `commit:true` 与 `commit:false` 后 `commit()` 两种路径均失败；只换清单、连日期一起修改都失败。系统解释是该账户不支持通过此 API 在清单之间移动。清单属于同一 iCloud CalDAV source，允许内容修改；原清单改日期、目标清单创建、删除都正常。
- Reminders 的 `move reminder id … to list id …` 成功。list id 与 EventKit calendarIdentifier 相同，reminder id 是 `x-apple-reminder://` 加本地 calendarItemIdentifier；当时 external/local ID 相同。移动后事项编号改变，旗标、备注、优先级、全天截止日期、创建时间保留；move 本身没有返回值。第一次后台启动 App 约 7 秒。
- 负责进程的启动链经过 `/usr/bin/osascript`。自动化权限另于提醒事项/日历权限，首次可能弹窗；拒绝、超时可能返回 -1743/-1712。技能历史中有 -1712 伴随自动化拒绝的情况，因此超时提示也提醒检查自动化权限。

源码追溯：`git log -S commitChanges` 显示既有设计有意采用整组 originals 预检、暂存和单次 commit；此前 prepare 失败按未执行、commit 失败按 unknown。此次外部移动打破“commit 前必然没有持久写入”的前提，必须单独跟踪移动是否已发生。

## 改动文件

| 文件 | 修改 |
| --- | --- |
| `native/EventKitBridge.swift` | 可替换 move runner、UUID 校验和 descriptor 参数、移动规划/找回、旧新引用替换、整组预检与部分成功分类、条件看门狗、纯替身自检 |
| `native/Info.plist` | 加入 `NSAppleEventsUsageDescription` |
| `apple.py` | 固定原因白名单、原生 unknown 明确解析、换清单组默认 helper 限时 |
| `apple_diagnostic.py` | phase 白名单加入 move/reconcile，其余脱敏字段不扩张 |
| `schedule.py` | 按组内容选预算，预算不足不启动 helper，保留固定原因到 finish 回执 |
| `test_applescript_move.py` | 原生结果到 finish/重放的分类、预算、损坏管道与超时、固定文案测试；Apple/网关均替身 |
| `README.md` | 当前实现、权限、时间、编号限制与验证状态 |
| 本记录、`custom/records/README.md` | 完整证据与索引 |

`helper_process.py` 已审阅，无需修改：其保留 spawn 句柄、取消时 kill/reap、capture active 登记仍适用。NSAppleScript 不产生 osascript 子进程，故不需增加进程树清理。所有 contracts 和 fixtures 保持原样。

## 实现与取舍

采用**直接 AppleScript**，不先让 EventKit 试错。已知 iCloud 路径必然失败，试错只会增加延迟与半截暂存的风险；仅新建提醒事项仍通过 EventKit 指定 calendar。其他账户是否支持 Reminders move 以应用响应为准，跨账户场景未验收。

选择进程内 **NSAppleScript**，理由是现有 helper 本来就在独立进程，父进程 kill/reap 与 SIGALRM 可约束它，不增加父 helper 退出后仍活着的 osascript 子进程。它不会改变 macOS 对负责进程的归属规则；权限条目必须实际验收。用 `executeAppleEvent` 调用静态脚本 handler，两个 ID 作为 NSAppleEventDescriptor 数据参数；不是将数据拼入 AppleScript。两者要求 36 字符 UUID；不按事项标题、清单名选移动对象。脚本变量不使用 before/after。

流程：

1. 沿用整组 originals 检查；完成、删除、缺快照或生成后有变化均整组 skipped。
2. 规划 update 的最终清单，验证目标唯一且可写、reminder 未完成且非重复、有 creationDate、external/local ID 相同、ID 合法。同一事项多次换清单折叠到最终目标；按第一次涉及的顺序排列不同事项。清单解析可用配置中的名称对应表，发送 move 时始终是精确 calendar ID。
3. 对其余字段先做一次 `commit:false` 整组预检，并 reset 丢弃；此阶段不发送 move、不 commit。预检也检查 merge/delete/to_event 的顺序与对象有效性，避免后续明显无效变更造成无谓移动。
4. 每个移动前重新读取旧事项和目标清单。旧事项必须仍与完整现场快照一致；目标不能已有同标题、creationDate、未完成的候选。此处最多等 5 秒。预算不足或预条件失败且此前未移动时，未执行。
5. NSAppleScript 发送一次 move，命令回复超时 12 秒。-1743 是明确授权拒绝；其他执行结果均先核对，绝不自动重发 move。
6. 移动后 reset EventKit 缓存，在最多 5 秒窗口里轮询：旧本地 ID 已不存在，目标清单内按**原始标题 + 完整 creationDate + 未完成**恰好找到一条新 ID；不能是原 ID。不唯一、两者同时存在、查不到或读取失败均不能冒认成功。
7. 找回的新 ID 仅存本次 helper 内存映射。全部移动结束后，重新解析本组所有引用、复核未移动项完整快照及已移动项除 list/modified_at 外的快照，再按原 changes 顺序暂存其他字段，最后一次 commit。merge 的 `into` 可指向已移动的目标；merge 源在后续操作中依然被移除。没有移动的组走单次 prepare/commit，不调用 AppleScript。

整组有多个移动时逐个执行；每次发送前要求 48 秒软预算剩余至少 27 秒，给前读、命令回复、后读和后续处理留空间。它不是跨 AppleScript/EventKit 的事务：第二次移动失败、预算不足、后续字段暂存失败或 commit 不确定，都不能回滚第一次移动。本合同不能表达部分持久完成的字段集合，所以整组 unknown、每条结果 failed 并给固定说明；不把已移动等同于全部 done，也不执行补偿移动。其他独立依赖组仍可继续。

### 超时分类的刻意收紧

Apple 官方明确说明：`with timeout` 只停止脚本等待，**不会取消应用已经接收的操作**。因此单凭 EventKit 当前还能读到旧事项，无法证明迟到 move 不会发生。实现对 -1712/-609 保留这一边界：先核对，唯一找到新项则继续；只看到旧项未变仍 unknown。它比初始任务“读不出来才算 unknown”更保守，避免无法证明的未执行回执导致重试。已在会话中提出可选确认；未收到相反选择时采用此默认。

参考：[NSAppleScript 官方接口](https://developer.apple.com/documentation/foundation/nsapplescript)、[AppleScript with timeout 语义](https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_control_statements.html)。这些文档支持 API/超时语义，不代表本机真实权限或 iCloud 验收。

## 结果分类

| 观察/阶段 | helper / operation | 公共条目 | 重试策略 |
| --- | --- | --- | --- |
| originals 缺失、事项变化/完成/删除 | skipped / succeeded | skipped + 原原因 | 不写入 |
| 首次 move 前预检、编号或预算拒绝 | not_executed / failed_not_executed | failed + 固定原因 | 仅网关新许可可重试 |
| -1743，且此前无移动 | not_executed / failed_not_executed | 没有控制「提醒事项」的权限，未执行 | 授权后由新许可重试 |
| -600 或普通明确错误，重读证明原项未变且无目标候选，且此前无移动 | not_executed / failed_not_executed | 「提醒事项」无法打开，未执行 / 移动失败，未执行 | 仅新许可重试 |
| -1712/-609 后，旧 ID 消失且唯一找回新项 | 继续暂存和 commit | commit 成功为 done | 不重发 move |
| -1712/-609 后，只看到旧项未变 | unknown / unknown | failed + 超时/连接中断、需核对 | 不可自动重试 |
| 成功回复但未唯一找回、候选多条、旧新同时存在、读取失败 | unknown / unknown | failed + 已移动，但无法确认移动后的事项，需核对；不可重试写入 | 不可自动重试 |
| 前一个移动已确认，后续移动/预算/prepare/commit 失败 | unknown / unknown | failed + 部分已移动，其余修改结果未知，需核对；不可重试写入 | 保留回执，人工核对 |
| helper 超时、SIGALRM、取消、管道断裂或坏回执 | unknown / unknown | failed + 结果未知 | 父进程终止 helper，不重写 |
| 全部移动和最终 commit 成功 | done / succeeded | done，不附 reason | 同业务组重放原回执 |

用户文案只取固定 reason_code 映射，不包含系统原文、标题、清单名或编号。日志沿用 domain/code/phase/op/fields 和既有 immediate underlyingError 身份白名单；只增加 move/reconcile 两种 phase。不记录 userInfo、localizedDescription、AppleScript 源码、参数或候选内容。现有日志前缀 `EventKit failure` 保留兼容性，domain 可为 OlloAppleScript。

### 设计复核后的界面文案

界面只显示下列指定文案；技术说明和权限授予步骤保留在文档中。成功结果不附 reason。本轮仅调整文案，不改变结果分类。

| reason_code | 用户可见文案 |
| --- | --- |
| move_permission | 没有控制「提醒事项」的权限，未执行 |
| move_timeout | 「提醒事项」响应超时，结果未知，需核对；不可重试写入 |
| move_app_unavailable | 「提醒事项」无法打开，未执行 |
| move_connection_lost | 与「提醒事项」的连接中断，结果未知，需核对；不可重试写入 |
| move_failed | 移动失败，未执行 |
| move_precondition | 确认前事项或目标清单已变化，未执行 |
| move_budget | 处理超时，未执行 |
| move_unresolved | 已移动，但无法确认移动后的事项，需核对；不可重试写入 |
| move_partial | 部分已移动，其余修改结果未知，需核对；不可重试写入 |

## 逐层时间预算

| 层 | 之前 | 此次 |
| --- | --- | --- |
| AppleScript 命令回复 | 无 | 12 秒；App 启动/编译阻塞仍由外层硬限约束 |
| 每次移动前完整读取 | 无 | 最多 5 秒 |
| 每次移动后核对窗口 | 无 | 最多 5 秒；约 0.2 秒轮询间隔，不重试写入 |
| apply 移动协调软预算 | 无 | 48 秒；新 move 要求剩余至少 27 秒 |
| helper stdin 未读完/普通请求看门狗 | 7 秒 | 不变；只在已解析含 list update 的 apply 后重设为 55 秒 |
| Python group helper_limit | 10 秒 | 含非 null list update 为 58 秒；其余 10 秒 |
| schedule 每组 budget | 14 秒 | 含非 null list update 为 62 秒；其余 14 秒 |
| claim / finish | 最多 2 秒 / 每次最多 1 秒，finish 最多 2 次 | 不变；移动组若不能提供完整 58 秒则在启动 helper 前拒绝 |
| capture / lookup / items | helper 6 / 5 / 5 秒，外层 8 / lookup 原规则 / 6 秒 | 不变；capture 硬看门狗仍为 7 秒，维持其许可到期 + 30 秒核对合同 |
| collect | 父进程 6 秒，helper 7 秒 | 不变 |

Ollo 当前源码（非运行配置）核对：

- `core/schedule_port.py` 的公开/bridge 5/3、10/8、8/6、20/18 秒预算只适用于 wake/capture/items/interpret；GTD 确认通过 Hermes run/tool，不走其中的 capture 预算。
- `storage/schedule.py::claim` 对 gtd_group 的 `permit_expires_at` 为 None；不会在 8 秒后释放移动许可。unknown/executing 继续持有回执；unknown 还会让该报告的 active confirmation 保持待核对。
- `agents/hermes/config.py` 的请求超时默认 30 秒，SSE 读取空闲超时默认 90 秒；`client.py::open_events` 单独用后者。确认 POST 接收 run 后返回，30 秒不是整个确认的结果等待上限。
- Hermes `api_server_runs.py` 的 run SSE 在等待期间每 10 秒发 keepalive；Ollo 对 App 的 SSE 另每 15 秒发心跳。单组预算小于默认 90 秒，多个组可继续在一个有心跳的 run 中执行。没有修改这两端的核心超时，也没有读取线上配置来确认它们使用默认值；部署时必须核对实际 SSE/反向代理/客户端是否另设更短限制。

## 编号变化与不改合同的边界

只读检查了 Ollo `server/src/ollo_gateway/` 与 `contracts/`：

| 使用方 | 当前行为和本次后果 |
| --- | --- |
| `storage/schedule.py::prepare_confirmation`、`gtd_change_receipts` | 按 report_id 与规范化 changes 指纹建立操作/回执，changes 保存旧 item/into；完全相同的业务组仍命中原操作，重复确认不会再移动 |
| `finish` / `_metadata` / `_save_result` | 按组索引保存并重放真实结果；done 不附 reason；没有重写报告 ID 的逻辑 |
| 同组后续 update/merge | helper 内存别名绑定新对象，已处理；不持久化私有映射表 |
| 同一报告的后续卡片、新的确认内容 | 仍带旧 item/into 与 generation originals；旧项查不到即 skipped，不会按标题猜新项。完全相同组重放已完成回执 |
| `storage/inbox.py::confirm_focus`、`report_focus` | 存报告内旧 item_ids；移动不会更新它们。历史重点仍是旧报告引用，无法自动关联当前新事项 |
| `core/schedule.py::items/interpret`、bridge `schedule.items` | 允许集合来自持久报告，只能查旧 ID；items 返回 exists=false。新 ID 既不在报告引用里也不在 originals 授权里，不能私自替换后继续写入 |
| `openapi.yaml::GTDResultEntry` | additionalProperties=false，仅 index/status/reason；没有可复用的 item_id/replacement_id。capture 的 item_id 不适用于 gtd_group |

成功结果只返回 done，不附 reason；编号变化和需要重新生成报告的限制只保留在 README/本记录中，不在确认界面提供解释或操作指南。刷新旧报告或仅重新查询旧 items 不会获得新 ID；只有新采集、新报告及其 originals 能签发新确认。没有把 ID 写入 reason 形成隐式协议，没有修改不可变历史报告，也没有扩张 allowed_item_ids。

若需要跨卡片/重点/报告连续引用，另开合同变更：

1. 公共 `GTDResultEntry` 增加可选 `identity_changes: [{old_item_id,new_item_id}]`（名称待两端定稿）。数组能表达同组多次移动，不只一个 new_id。需要明确它表示已确认移动，不自动证明整个 change done。
2. 对“已移动但其余失败”还需 bridge outcome 能携带已确认映射/阶段事实；不能只在 succeeded 下放映射，或把 partial 冒充 done。保持 unknown 的不可重试语义。
3. 网关在 finish 同一事务内验证映射来源与 permit，将映射附着操作回执，维持原 changes 指纹；增加当前引用投影/查询解析，保留报告快照不变，处理 focus 与 items 响应 ID 对应及 allowed IDs/originals 授权边界。
4. Hermes 同步离线 schema，发送真实映射；Ollo 同步源合同、导出 schema、网关存储/服务及 App 的解码、卡片状态和重点引用。新增“重放不再次移动、旧报告不能凭映射扩大写授权、多次移动和部分失败”两端测试。

以上**均未实施**；不改合同的代价就是旧报告后续操作需要重新生成报告，不能承诺透明续用。

## 验证结果

- 设计会话沙箱外复核（用户 2026-10-15 提供，本轮文案调整前）：`hermes-plugins/` **26 个文件、553 项全过**；helper `--self-test` 返回 `{"ok":true}`。本轮按复核意见逐字替换九条失败原因、移除成功 reason、同步结果断言，并把索引移回表格末行；分类、预算、合同均未改。本轮调整后的 Python 回归仍需沙箱外补跑。
- 本轮文案调整后：在新的 `ollo-move-20261015-copy-review` 开发目录重新编译 helper，`--self-test` 返回 `{"ok":true}`；修改 Python 文件的 `py_compile` 与 `git diff --check` 通过。定向运行 `test_applescript_move.py` 时，runner 在收集前报 `PermissionError(1, 'Operation not permitted')`：**1 个文件、0 collected / 0 passed**，没有执行测试，需沙箱外补跑；不将此前的 553 项结果当成本轮复测结果。
- Swift：macOS `swiftc -O -assert-config Debug`，链接本次 Info.plist，在新的开发目录构建成功；运行 `--self-test` 返回 `{"ok":true}`。没有构造 EventStore、发送 Apple event 或申请权限。
- 原生进程看门狗烟测：保持 stdin 打开、不提供请求，helper 在构造 EventStore 前由 SIGALRM 终止（returncode=-14，7.06 秒），确认普通请求仍保留启动起 7 秒限制。
- Swift 替身覆盖：移动后旧新编号替换、其余字段在移动后写入、-1743 拒绝、-1712/-609 未变仍 unknown 与已移动可恢复、-600 未执行、找回多条/字段不符/读取失败、后续 prepare/commit 失败、多移动全部完成/第二项失败/预算不足、merge 新目标绑定、只日期/delete/to_event 不走 runner、UUID 注入拒绝、诊断不泄露内容。既有 originals/日期/collection/diagnostic 自检也继续通过。
- 首轮开发在沙箱内未能运行 Python 新增的 14 个参数化用例。指定全量命令的激活阶段无法打开默认 uv cache，报 `Operation not permitted`。复用 PM 已存在且 `hermes_activation_current` 核验为当前 checkout 的隔离测试环境后，runner 的 18 个文件均在收集前报 PermissionError：**0 collected / 0 passed**；这次沙箱尝试不是通过，随后设计会话的沙箱外结果见上文。
- 保护运行 home：标准 runner 有 `$HOME/.hermes/pytest_live_guard.py` 的探测；为遵守本次禁止访问 home，使用一次性 runner 副本，只去掉这段 guard 探测并将 SCRIPT_DIR 指回仓库，其他环境清理/逐文件 runner 不变。核心源文件没有改。尝试额外 OS 拒绝访问规则时，沙箱拒绝 `sandbox_apply`，因此未使用该路径；没有申请扩大权限。
- `scripts/check --base HEAD`：通过隔离**测试**解释器执行，12 项中 health 等 10 项通过；health 为 0 blocking / 0 advisory。全套仍失败于历史 no-tmp 文档项、23 项 ruff。将诊断涉及文件的 HEAD 内容逐个用同版本 ruff 重查，23 项的文件/规则/文案完全相同，本次未新增；新增测试文件显式 ruff 检查无发现。没有为消除这些历史问题修改无关文件。
- `git diff --check`、修改 Python 文件的 `py_compile`、Info.plist 的 `plutil -lint`：通过。最终改动路径核查符合边界；保护记录仅保留会话开始时已有的修改。真实 App/TCC/iCloud/SSE 链路未验证，不能用纯替身或编译成功代替上线验收。

Swift 复测（只运行 `--self-test`，普通 stdin 调用会访问真实 EventKit）：

```sh
ollo_build="$HOME/.cache/hermes-agent-dev/ollo-move-20261015-review"
mkdir -p "$ollo_build"
xcrun swiftc -O -assert-config Debug -module-cache-path "$ollo_build/modules" \
  hermes-plugins/ollo-bridge/native/EventKitBridge.swift \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
  -Xlinker hermes-plugins/ollo-bridge/native/Info.plist \
  -o "$ollo_build/ollo-eventkit"
"$ollo_build/ollo-eventkit" --self-test
```

用户指定的 Python 命令，需由设计会话在沙箱外复测；若仍禁止运行 home 的 guard 探测，应沿用上述一次性 runner 副本方式，不能直接执行该 guard：

```sh
bash -c 'export HERMES_RUNTIME_DIR=$HOME/.cache/hermes-agent-dev/tools HERMES_HOME=$HOME/.cache/hermes-agent-dev/home; source ./activate; scripts/run_tests.sh -j 2 hermes-plugins/ollo-bridge --file-retries 0'
```

没有在基线真实提醒事项上做红灯实验；用户提供的 -3002 实测是前置故障证据。设计会话已提供修改后全量通过结果，但没有基线红灯结果；本轮文案调整后的 Python 回归仍需沙箱外跑，不将这些结果合称红绿已证实。

## 后续部署与人工验收（另行授权后执行）

1. 设计会话在本轮文案调整后于沙箱外复测完整 Python 测试和 Swift 自检，确认新分类、重放、纯日期/删除/转日历无回归；核对实际网关 run SSE 空闲限制不短于本组预算或可靠接收心跳。
2. 在部署窗口暂停新的 GTD 确认，保留已有 unknown/executing 回执，等待在途操作结束。按既有发布流程保存当前插件/helper 版本；部署完整插件 Python 与新 helper、配套 Info.plist，保持配置中 helper_path 与实际二进制一致。按现有签名流程处理 helper，不覆盖开发构建目录作为发布方案。本次未做上述任何一步。
3. 用户在 **Mac mini 屏幕**登录桌面，保留提醒事项 App 可正常启动。通过真实负责进程启动链发起一张专用测试事项的换清单确认，遇到自动化弹窗由用户点允许；到「系统设置 → 隐私与安全性 → 自动化」核对负责进程（预计 osascript，以实机为准）可以控制「提醒事项」。EventKit 权限如缺失另按原 `--authorize` 流程授予。不要用从 Terminal 手动运行 helper 的成功替代 LaunchAgent 链授权验收。
4. 在专用测试清单/事项上验收换清单及换清单+日期/标题：目标只出现一项，旧 ID 不存在、新 ID 不同，创建时间/备注/旗标/优先级/全天日期保持；明确要求的新字段已更新。核对 Ollo done 回执不带 reason，不出现编号解释或操作指南；同业务组重复确认不得再次移动。
5. 验收 merge 源/目标关联组、多个移动、仅日期、delete、to_event。后两项有真实删除含义，只用专用测试事项。模拟拒绝权限时应未执行；模拟超时/连接中断必须先核对，已移动可成功恢复，无法证明完成时 unknown 且不盲重试。部分移动后失败必须明确说明已发生部分移动。
6. 用旧报告查看 items（预期旧项不存在）、旧重点选择和后续卡片（不应偷偷改写新项）；重新采集生成新报告应带新 ID 和 originals，新的确认才能继续操作。验收回执保存、App/SSE 展示与用户可见固定原因，日志不能出现事项内容或编号。
7. 若需回滚，先停止新确认，保留移动后的实际事项及现有回执；回滚代码不能自动把事项搬回原清单，也不能清空 unknown/回执来重试。人工核对并重新生成报告后再恢复服务。

## 执行记录（2026-10-15，设计会话，用户逐项同意）

- **提交**：fork `custom` = `fc48a8fa16`（在 `b91c8e5a9c` 之上快进推送）。沙箱外：`hermes-plugins/` 26 文件 553 项通过；helper `--self-test` 为 `{"ok":true}`。
- **部署**（22:38）：备份到 `~/.hermes/backups/applescript-move-20261015-223834/`（旧插件、旧 helper、暂存目录）；rsync 插件（排除 `__pycache__`、`.pytest_cache`、`contracts/export*`），与源码 `diff -rq` 一致；helper 用 `swiftc -O` 加 Info.plist 编译、ad-hoc 签名（标识 `com.birderyu.ollo.bridge.eventkit`）、自检后替换，与构建产物 `cmp` 一致。
- **重启一次**：22:38:41 `hermes gateway restart`，22:38:48 ollo-bridge 已挂载、ollo 与 api_server 重新连接；launchd 的 program 仍为 `/usr/bin/osascript`，LaunchAgent plist 与 `~/.hermes/backups/pre-v0.22.1-20261015/ai.hermes.gateway.plist` 逐字一致；重启后日志无 ERROR、无 session_reset 警告。
- **自动化授权与真实链路**：用一次性 no_agent 任务（`create_job(paused)` + `trigger_job`，由网关调度器执行，投递 local），在网关进程里依次 capture 测试事项 →  items → 与卡片确认相同的 apply（`{"op":"update","list":"工作","due":"2026-10-19"}`）。
  - 第一次 22:40：Mac mini 弹出「osascript 想要控制提醒事项」，12 秒内未点，结果 `unknown / move_timeout`（诊断 `OlloAppleScript -1712`，phase reconcile），事项未移动。用户随后点「允许」。
  - 第二次 22:42：apply `done`，11.4 秒；同一任务里用 `~/.local/bin/remindctl edit --list 收件箱` 移回，11.3 秒，编号改变，截止日期、备注、创建时间保留。
  - 两条测试事项、两个一次性任务和 `~/.hermes/scripts/ollo_move_check.py` 已删除。
- **remindctl**：用户 fork `birderyu/remindctl` 的 `custom` 分支 `85c5664`（AppleScript 兜底），`make test` 132 项通过；`make build` 产物复制到 `~/.local/bin/remindctl`（ad-hoc 签名）。Hermes LaunchAgent 的 PATH 里 `~/.local/bin` 排在 `/opt/homebrew/bin` 前，Hermes 因此用这份；终端里可能仍是 Homebrew 0.3.2。版本号显示 0.3.7（沿用 fork 的 version.env）。**回滚**：删除 `~/.local/bin/remindctl` 即退回 Homebrew 版。Hermes 升级后核对它仍在。
- **留意**：AppleScript 移动的超时是 12 秒，真实链路一次 apply 用了 11.4 秒（含找回新事项与提交），余量不大。以后若出现 `move_timeout`，先看提醒事项 App 是否冷启动，再考虑把超时调大（helper 总预算 55 秒，够用）。
