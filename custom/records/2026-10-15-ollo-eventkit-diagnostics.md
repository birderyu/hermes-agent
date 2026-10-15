# EventKit 失败诊断与只读结构检查

2026-10-15 开发准备，用户于 2026-10-14 同意诊断改动。仅修改独立 fork 开发副本的 `custom` 工作树；未访问运行 home，未部署、重启、提交或推送。开始时已有正式切换方案、脚本、测试及文档改动，全部保留。本记录不代表那些工作已经在本会话执行。

## 现场与证据边界

以下来自用户提供的 2026-10-14 21:05 设计会话只读核对，本会话未接触真实 Apple 数据：

- GTD 正式切换后的第一次真实确认共四组、分别领取：两个 delete 成功；两个 update 同时改短标题并从收件箱移至工作清单，返回 unknown，文案为「写入结果未知，需核对；不可重试写入」。工具约 0.63 秒返回，不是超时。
- 两条事项事后按 ID 查询仍在原 iCloud 收件箱，标题仍是原长文本；标题长度分别 298（含换行）/157，备注长度 347/197，未完成、无日期，来源为工作群分享。六个清单属于同一 iCloud 账户。10-12、10-13 在测试清单上的同类更新和移动曾多次成功。
- 现场支持此次修改未落地，但不能据此定位 EventKit 拒绝原因；事项结构可能有关，尚未证明是标题、分享来源或其他属性导致。旧 helper 没有保留 NSError，历史错误无法用本补丁追溯恢复。
- 当前源码核对：`prepare` 用 `commit:false` 暂存；失败为 not_executed；`store.commit()` 抛错为 unknown。两处此前均丢弃错误域和错误码。仍不把 `reset()` 视为跨设备原子回滚证明，也不改变历史 unknown 或重试授权。

本记录不写真实 confirmation/operation/item ID 或事项内容。

## 实现

- `native/EventKitBridge.swift`：`MutationFailure` 返回固定 error、原 outcome 和 `diagnostic`。只序列化 `phase`、NSError `domain`/整数 `code`、`op`、`fields`；直接 underlyingError 仅取 domain/code，不递归，不输出 userInfo、localizedDescription、标题、备注、清单名、ID 或 URL。
- `prepare` 失败标记当前暂存操作；`commit` 失败标记整组。相同操作组成的组保留该 op，混合组为 `batch`。fields 是有效请求中的可写字段名（null 按既有语义忽略），混合组取并集；不含 item/into/by，不代表这些字段已经写入。capture 记录 title/list/notes/url 字段名。
- `apple.py` 只将过滤后的诊断附在 `NotExecuted` / `UnknownWrite` 异常属性上；`apple_diagnostic.py` 再按白名单过滤并生成单行日志；`schedule.py` 在持有 operation_id 的失败分支记录一次 WARNING。观察已保存回执不会再记同一条日志或调用 helper。
- 日志固定为 `EventKit failure <紧凑 JSON>`，JSON 仅有 operation_id 和 diagnostic。无异常堆栈、stderr、原 helper 响应或业务请求。网关 finish、工具返回和用户文案都不带诊断。旧 helper 没有 diagnostic 时照常分类，不输出该 WARNING。
- 新增原生 `inspect`，仅供操作员直接调用 helper，没有新增 HTTP、模型工具、写入或重试入口。它只按精确 ID 查询 EKReminder，返回顺序与输入顺序一致，不回显 ID。额外参数（包括 changes）被拒绝；事件对象、重复外部标识造成的歧义或读取失败报固定错误，不伪装为事项不存在。

诊断示例（全为合成值）：

```json
{"error":"EventKit operation unavailable","outcome":"unknown","diagnostic":{"phase":"commit","domain":"EKErrorDomain","code":17,"op":"update","fields":["list","title"],"underlyingError":{"domain":"NSCocoaErrorDomain","code":42}}}
```

错误码示例不代表此次故障的实际错误码。

## inspect 协议

stdin 必须是且仅是 `{"method":"inspect","ids":["ID_1","ID_2"]}`，1–100 个非空 ID。与既有 lookup 一样，先匹配 external identifier，找不到再匹配本地 identifier；不按标题搜索。不需要 apple 名称映射。普通执行不请求授权，inspect 仅要求提醒事项 full access。

stdout 为 `{"items":[第一条结构,第二条结构]}`。不存在仅返回 `{"exists":false}`；存在时只包含：

| 字段 | 含义 |
| --- | --- |
| exists | true |
| hasNotes / hasAlarms / hasRecurrenceRules / hasAttendees | 直接读取 EKCalendarItem 的布尔属性；EKReminder 继承这四个属性 |
| urlIsEmpty | URL 缺失或空，不返回 URL |
| calendar.sourceType | EKSourceType 原始整数，缺失为 null；2 表示 CalDAV，不能单凭它断定 iCloud 账户身份 |
| calendar.allowsContentModifications | 清单允许修改的布尔值，缺失为 null |
| identifiersEqual | external identifier 与本地 identifier 是否相等；外部 ID 缺失时为 false，不返回任一 ID |
| titleLength | Unicode scalar 数量，不是 UTF-8 字节数或组合字形数 |
| titleHasNewline | 是否包含 Foundation CharacterSet.newlines 中的字符 |

长度和布尔值用于结构诊断，不能证明写入一定成功，也不能重建历史 NSError。`inspect` 不读取备注正文，不 save/remove/commit/reset，不触发授权请求；已有 7 秒看门狗仍生效。

## 本次验证

- 已尝试整个 `hermes-plugins/ollo-bridge`，使用维护文档指定的独立开发测试解释器和 `scripts/run_tests.sh` 的临时副本。原 runner 启动会探测真实 home 的 `pytest_live_guard.py`；临时副本只去掉这段探测并固定仓库 scripts 路径，保留清洗环境、逐文件子进程和重试规则，仓库 runner 未改，也没有使用裸 pytest。
- **15 个文件均在子进程启动时报 `PermissionError(1, 'Operation not permitted')`，0 collected，不能记为通过。** 包含新增诊断测试和既有正式切换测试。没有退回旧的通过数作为本补丁证据。
- Swift `-frontend -parse` 和 `-typecheck` 通过；仅编译器静态检查，未生成/运行 helper，未创建 EKEventStore，未做真实事项检查。
- `scripts/check --base HEAD`：11 项中仅未改历史文档 `custom/records/2026-08-30-v0.20.6.md:27` 的 no-tmp 失败；code health 为 0 blocking / 0 advisory。最终 5 个相关 Python 文件的 AST、5 段 bash 命令语法、相对文档链接及 `git diff --check` 均通过；补完文档后定向 no-tmp 复查仍仅同一既有失败。
- 新增 Python 用例覆盖 prepare/commit、capture/group、含事项内容的错误替身、underlyingError 过滤、非法诊断、旧 helper 兼容、一次日志、网关回执及用户结果脱敏、重复观察不再次写入。Swift 自检新增 NSError 隐私与分类、字段白名单、只读结构映射及拒绝写入形状请求，使用在 `-O` 下仍有效的 `precondition`。既有原生测试编译加入 `-O -assert-config Debug`，使原有 assert 也执行。

沙箱外须补跑下面的全部测试；这些命令尚未执行：

```bash
cd "$HOME/Projects/hermes-agent"
export HERMES_RUNTIME_DIR="$HOME/.cache/hermes-agent-dev/tools"
export HERMES_HOME="$HOME/.cache/hermes-agent-dev/home"
source ./activate                 # 使用 bash；仅独立开发环境
scripts/run_tests.sh -j 2 hermes-plugins/ollo-bridge --file-retries 0
python scripts/check --base HEAD
```

runner 在沙箱外仍会探测真实 home 的 live guard；若该会话也禁止任何运行 home 访问，沿用上述临时 runner 处理，不直接执行原 runner。测试只覆盖 bridge 目录，不运行可能操作真实网关的全仓服务管理测试。

## 编译与自检（沙箱外，尚未执行）

使用 bash 的独立终端，输出到新的开发构建目录，不覆盖线上 helper。`--self-test` 分支在创建 EKEventStore 前运行，不访问真实事项或请求权限。

```bash
set -euo pipefail
REPO="$HOME/Projects/hermes-agent"
BUILD_DIR=$(mktemp -d "${TMPDIR:-/private/tmp}/ollo-eventkit-diagnostic.XXXXXX")
NATIVE="$REPO/hermes-plugins/ollo-bridge/native"
/usr/bin/xcrun swiftc -O -module-cache-path "$BUILD_DIR/modules" \
  "$NATIVE/EventKitBridge.swift" \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
  -Xlinker "$NATIVE/Info.plist" -o "$BUILD_DIR/ollo-eventkit"
/usr/bin/codesign --force --sign - "$BUILD_DIR/ollo-eventkit"
/usr/bin/codesign --verify --strict "$BUILD_DIR/ollo-eventkit"
"$BUILD_DIR/ollo-eventkit" --self-test
```

期望恰好 `{"ok":true}` 和退出码 0。新的隐私和 inspect 检查在普通 `-O` 下也生效；全套测试中的 `-assert-config Debug` 还覆盖旧的 assert 自检。签名成功或自检通过均不等于实际 EventKit 权限及线上功能验收。

## 部署、验证与一次重启（需另行同意）

同意节点：

1. 沙箱外开发测试、构建和自检无需访问真实数据，但必须在本任务结束后由设计会话执行并记录结果。
2. **另行同意访问实际配置路径、备份/替换已部署插件与 helper，以及一次 `hermes gateway restart`**。当前任务明确不授权这些动作。先核对当前 helper_path、目标 Profile/home、完整待复制插件差异，确保没有正在进行的确认/捕获/helper、也不临近 cron 触发。文件替换期间暂停发起新操作；条件无法满足时先停止部署准备，不擅自增加停机/重启。
3. 设计会话按用户要求在沙箱外只读检查这两条事项；这里只交付调用方法。若新二进制缺权限，需要用户明确允许授权后才运行 `--authorize` 或在系统设置放行；inspect 自己不会申请权限。
4. **本补丁不授权重试两条旧 unknown、重新确认、修改回执或删除执行状态。** 后续写入验证需针对明确选定的测试事项取得单独同意，不能为获取 NSError 重放旧写入。

下例沿用既有部署记录的默认 home/helper 路径，**本会话未核验线上路径**。设计会话必须先核对，不匹配则用实际值替换变量，保持 bridge.json 原样。使用已安装 Hermes 入口，不能使用激活开发环境后的 shell 函数。完整插件复制包含工作区已有切换工具，须审阅完整候选再同意。

在上节构建成功后，用户同意部署才执行：

```bash
set -euo pipefail
OLLO_HOME="$HOME/.hermes"                       # 先确认实际所属 home
HELPER_PATH="$OLLO_HOME/ollo/bin/ollo-eventkit"  # 先确认现有 helper_path
HERMES_BIN="$HOME/.local/bin/hermes"            # 已安装入口，先核对
PLUGIN_PATH="$OLLO_HOME/plugins/ollo-bridge"
BACKUP_DIR="$OLLO_HOME/backups/eventkit-diagnostic-$(date +%Y%m%d-%H%M%S)"
test -d "$PLUGIN_PATH"
test -f "$HELPER_PATH"
test -x "$BUILD_DIR/ollo-eventkit"
test -x "$HERMES_BIN"
umask 077
mkdir -p "$OLLO_HOME/backups"
mkdir "$BACKUP_DIR"
cp -pR "$PLUGIN_PATH" "$BACKUP_DIR/ollo-bridge"
cp -p "$HELPER_PATH" "$BACKUP_DIR/ollo-eventkit"
STAGE_DIR=$(mktemp -d "$OLLO_HOME/plugins/.eventkit-diagnostic.XXXXXX")
mkdir "$STAGE_DIR/ollo-bridge"
rsync -a --exclude='__pycache__/' --exclude='.pytest_cache/' \
  "$REPO/hermes-plugins/ollo-bridge/" "$STAGE_DIR/ollo-bridge/"
HELPER_STAGE=$(mktemp "${HELPER_PATH}.new.XXXXXX")
cp -p "$BUILD_DIR/ollo-eventkit" "$HELPER_STAGE"
/usr/bin/codesign --verify --strict "$HELPER_STAGE"
"$HELPER_STAGE" --self-test
mv "$PLUGIN_PATH" "$STAGE_DIR/previous-plugin"
mv "$STAGE_DIR/ollo-bridge" "$PLUGIN_PATH"
mv -f "$HELPER_STAGE" "$HELPER_PATH"
HERMES_HOME="$OLLO_HOME" "$HERMES_BIN" gateway restart
HERMES_HOME="$OLLO_HOME" "$HERMES_BIN" gateway status
diff -rq -x __pycache__ -x .pytest_cache \
  "$REPO/hermes-plugins/ollo-bridge" "$PLUGIN_PATH"
cmp "$BUILD_DIR/ollo-eventkit" "$HELPER_PATH"
```

只替换插件和 helper，不改任务、Skill、config、bridge.json、网关 DB 或旧回执；不调用正式切换脚本。上面的重启只有一次。若中途命令失败，停止后续操作，根据完成到哪一步恢复备份；不要继续运行半套新文件。

验证须记录：源/部署文件一致、helper 签名与自检、Gateway 新进程正常及插件加载正常，再进行已授权的只读 inspect。不得仅凭 restart/status 声称真实写入恢复。新 WARNING 仅在以后实际出现 prepare/commit 错误时产生；本次不得为了日志触发写入。

## 两条事项的只读调用

在已获读取权限的 helper 路径上调用（可用待测构建；若其权限未继承，则等已同意的替换完成后用实际路径）。下面只发 inspect，不发送 apply/capture/重试：

```bash
INSPECT_HELPER="$HELPER_PATH"  # 或 "$BUILD_DIR/ollo-eventkit"，先确认权限
python3 - "$INSPECT_HELPER" <<'PY'
import getpass
import json
import subprocess
import sys

ids = [getpass.getpass('第一条事项 ID（不回显）：'), getpass.getpass('第二条事项 ID（不回显）：')]
result = subprocess.run(
    [sys.argv[1]], input=json.dumps({'method': 'inspect', 'ids': ids}).encode(),
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False,
)
print(result.stdout.decode('utf-8'))
raise SystemExit(result.returncode)
PY
```

ID 仅在内存与 helper stdin，不出现在 shell 历史、进程参数或输出；结果的 items[0]/items[1] 分别对应输入。两条完整 ID 未提供给本会话，因此不填真实值。读取失败时保留固定错误，不改权限或自动切换为写入。

## 回滚（另行同意后执行）

备份同时保留旧插件和旧 helper，回滚两者，保留所有确认状态、DB、任务与 Apple 数据。需要与部署相同的无在途操作条件。回滚是异常恢复，需另一次明确同意的重启，不属于正常部署的那一次。

```bash
set -euo pipefail
test -d "$BACKUP_DIR/ollo-bridge"
test -f "$BACKUP_DIR/ollo-eventkit"
ROLLBACK_DIR=$(mktemp -d "$OLLO_HOME/plugins/.eventkit-rollback.XXXXXX")
cp -pR "$BACKUP_DIR/ollo-bridge" "$ROLLBACK_DIR/ollo-bridge"
ROLLBACK_HELPER=$(mktemp "${HELPER_PATH}.rollback.XXXXXX")
cp -p "$BACKUP_DIR/ollo-eventkit" "$ROLLBACK_HELPER"
/usr/bin/codesign --verify --strict "$ROLLBACK_HELPER"
mv "$PLUGIN_PATH" "$ROLLBACK_DIR/replaced-plugin"
mv "$ROLLBACK_DIR/ollo-bridge" "$PLUGIN_PATH"
mv -f "$ROLLBACK_HELPER" "$HELPER_PATH"
HERMES_HOME="$OLLO_HOME" "$HERMES_BIN" gateway restart
HERMES_HOME="$OLLO_HOME" "$HERMES_BIN" gateway status
diff -rq -x __pycache__ -x .pytest_cache "$BACKUP_DIR/ollo-bridge" "$PLUGIN_PATH"
cmp "$BACKUP_DIR/ollo-eventkit" "$HELPER_PATH"
```

本补丁没有数据迁移；回滚后失去新诊断和 inspect，但旧 unknown 的不可重试语义保留。备份及替换下来的插件不自动删除。

## 执行记录（2026-10-15，设计会话执行）

用户在 Ollo 会话中同意「现在就部署」和「在 Hermes 网关里用新程序重执行一次」（选项 2）。

| 时间 | 动作 | 结果 |
| --- | --- | --- |
| 07:00 | 沙箱外测试、编译 | `hermes-plugins/ollo-bridge/` 15 个文件 375 项通过；`swiftc -O`、ad-hoc 签名、`--self-test` 返回 `{"ok":true}` |
| 07:02 | 用新 helper 在设计会话终端只读 inspect 两条事项 | 两条均存在，iCloud（sourceType 2）、可修改，无提醒/重复/参与人/URL，有备注；标题 298 字含换行、157 字。结构上无异常 |
| 07:30 | 设计会话终端直接 apply、items | 均 `not_executed`：该终端只有提醒事项完整权限、没有日历完整权限，helper 在动数据前拒绝；没有改动 Apple 数据，也没有为此改系统权限 |
| 10:19 | 部署 | 确认无在途任务、下次 cron 为 21:00；备份 `~/.hermes/backups/eventkit-diagnostic-20261015-101933`（插件与旧 helper）；rsync 插件（排除 `__pycache__`、`.pytest_cache`、`contracts/export*`），源与部署一致；新 helper 签名校验与自检通过后替换，`cmp` 一致；`hermes gateway restart` 一次（10:19:44，约 15 秒），ollo-bridge 挂接，photon、hermes_plus、ollo、api_server connected；Ollo `verify` 对话与历史通过 |
| 10:20 | 一次性 no_agent 任务 `c69b6553f6ef`（暂停创建后 trigger，deliver=local），脚本与输入（600）放在 `~/.hermes/scripts`、`~/.hermes/ollo` | 10:21:03 运行：两条均 `skipped`「确认前已删除或缺少报告快照」——helper 按原 ID 找不到事项。设计会话随即只读复查，AppleScript 与 inspect 也都找不到这两个 ID（07:00 时仍在），用户随后确认是自己在 07:00 后手动删除；本任务没有改动任何事项 |
| 10:22 | 清理 | `remove_job` 删除任务，删除一次性脚本与输入文件；任务列表只剩 4 个正式 GTD 任务 |

结论：诊断版 helper 已上线，以后 prepare/commit 失败会在 bridge 日志里留下 `domain/code/phase/op/fields`。这两条的原始 NSError 已无法复现；Ollo 网关里这两条确认回执仍为 unknown（只作记录，不重试）。
