# 2026-10-14 Ollo GTD 正式切换方案（待执行）

目标是让原来投到 Photon 的四个 GTD 任务改投 Ollo，并读取真实清单和日历。本次只在独立 fork 开发副本准备方案、工具、测试和文档；没有访问运行 home，没有部署、重启、提交或推送。执行人是 Ollo 设计会话，下面每个授权节点都须由用户逐项同意后，在沙箱外执行。

现状取自用户提供的 **2026-10-14 09:0x 只读核对**：八个任务均 enabled，四个真实任务仍投 Photon；四个测试任务投 Ollo，bridge 和 Ollo 网关只接受测试 ID。采集、今日安排、工作日收件箱、测试卡片写入、A7 重点、随手记、我醒了已验收；每周回顾还没有自然运行过。本文不把这些转述当成本次重新验证。

## 切换后是什么样

| 保留的真实 ID | 名称（不改） | 上海时间 cron（不改） | script | kind | 删除的测试 ID |
| --- | --- | --- | --- | --- | --- |
| d83281b75902 | GTD-今日安排-工作日 | `0 8 * * 1-5` | ollo_collect_agenda.py | agenda | 36c04d45e58c |
| 8211bb91402a | GTD-今日安排-周末 | `30 9 * * 6,0` | ollo_collect_agenda.py | agenda | 4eabdf7d938b |
| 12d6987e974e | GTD-清理收件箱-工作日 | `0 21 * * 1-5` | ollo_collect_inbox.py | inbox | 188ed50c0816 |
| f9a5dd70ea34 | GTD-每周回顾-周日 | `0 21 * * 0` | ollo_collect_review.py | review | ec742022406f |

四个任务统一设置 `deliver=ollo:main`、`skills=[gtd, hermes-plus-gtd]`、兼容字段 `skill=gtd`、对应 script、`enabled_toolsets=[]`、`attach_to_session=false`。已有非空 `failure_deliver=photon` 一并改成 `ollo:main`；原来为空的继续继承主投递目标。只有报告模型是零工具，不改 Ollo 普通对话的工具配置。

ID、名称、schedule（含 display）、model、provider、repeat 及 completed 计数、next_run_at、运行记录和其他无关字段原样保留。不调用 `create_job`，不生成新 ID，也不重新计算下一次运行时间。运行 model 必须八个一致且非空，但不把文档曾写的型号硬覆盖到当前任务。保留无关任务。

`bridge.json.jobs` 用真实 ID 替换测试 ID，沿用原白名单条目的 title/kind；`agenda_jobs.weekday=d83281b75902`、`weekend=8211bb91402a`。删除整个 `apple` 字段。`apple_config.names()` 的缺省规则是逻辑名直接等于实际名，因此必须先由用户在 Apple App 核对：

- 提醒事项：收件箱、工作、生活、家庭、等待、长期。
- 日历：工作、生活、家庭。

每个命名空间内必须唯一；清单和日历可以各有一个“工作”。工具只核对配置及 helper 文件，不调用 EventKit，也不证明 Apple 里确实存在这些名称。不存在、重名或权限不足会使采集失败。`collection` 如存在必须等于默认范围，防止漏掉真实清单。

四个测试任务选择**在备份后删除**，不留暂停任务。原因是新旧任务同一时刻运行，暂停残留容易被误恢复；ID、完整定义和运行字段都在备份中，能原样恢复。`gtd-ollo-test` 整个目录删除；`hermes-plus-gtd` 保留原样。测试清单、测试日历及 Ollo 里的测试报告不删除。

## 提示词和 Skill 取哪一版

源码核对到 `2fc3602420`（2026-10-13 22:58）已把 A7 写入三个候选文档，不再是 10-11 初稿。正式 prompt 取 [四任务完整提示词](../../hermes-plugins/ollo-bridge/docs/cron-prompts-candidate.md) 中按表顺序排列的四个 `text` 块。

| 核对项 | 候选当前内容 | 正式取法 |
| --- | --- | --- |
| A7 今日重点 | 只由网关记住；不设今天到期、不顺延、不移动；不生成 agenda 确认 | 采用当前候选；Skill 也须包含 focus_confirmed 的读回规则 |
| 10-12 后的授权区分 | inbox/review 报告建议走卡片；用户直接请求列出具体变更并在对话确认后仍可走 Apple Skill | 采用当前候选，不把所有对话写入都改成卡片 |
| 未完成数、全部超期、无日期承诺、周末重点 | 先过滤 completed=false；无日期纳入候选；周末 1–2 个重点；不机械顺延 | 保留 |
| 采集失败、跨日 wake | Script Error 不自动变为 failure；采集日期可能不同于原 wake 业务日 | 保留限制，不能宣称已修复 |
| 候选说明曾固定 model/provider | 候选说明写有历史型号 | 此次以运行任务为准，保持原值；只安装四个 prompt 块 |
| 线上测试任务的逐字 prompt | 设计会话于 2026-10-14 在本机逐行核对：两个今日安排仅第一段仍为 A7 之前的卡片确认执行规则；收件箱、每周回顾与候选完全一致。本开发会话未读取线上文件 | 正式取候选 A7 版本；两个今日安排须分别用当前 prompt 的准确 SHA-256 显式放行，其他不一致仍停止 |
| 候选 gtd 差异 vs 已验收 gtd-ollo-test | 差异文档不是可直接安装的完整 gtd | 从运行测试 Skill 生成正式版，候选只作核对清单 |

设计会话确认，旧段落要求建议经卡片确认后由 `execute_gtd_confirmation` 执行；候选已改为今日重点由 App 卡片交给网关记住，不改提醒事项，不生成 agenda 确认，不调用该执行入口。本次明确选择候选，无需把旧段落回填候选。工作日测试 ID 为 `36c04d45e58c`，周末为 `4eabdf7d938b`；两份当前 prompt 的哈希由设计会话本机计算并填写下面占位，不把完整运行提示词贴入仓库。

`plan`/`apply` 接受可重复的 `--accept-prompt-drift <测试ID>=<当前prompt的SHA256>`，仅在该 ID 的当前哈希准确匹配时放行其 prompt 差异；未指定的差异、哈希不符、其他字段不符仍 STOP。内容相等判断仍只忽略首尾空白；为使放行绑定完整内容，输出的 `current` 长度及哈希、参数中的哈希都取 jobs.json 中 prompt **完整字符串的 UTF-8 字节，不先 strip**，候选哈希取实际将安装的正文（去首尾空白）。使用 64 位小写十六进制值。plan 输出 `accepted_drift: true` 及两边哈希；apply 会重新读取、校验，并始终安装候选。已完成切换后测试任务不再存在，这些参数不再产生放行记录，重复 apply 仍返回 noop 并保留首次 manifest。发现新的差异应在私有环境重新核对，不能沿用旧哈希扩大放行。

正式 `gtd` 来自已验收的测试 Skill 整个目录，含配套文件。工具只把 frontmatter 的 `name` 恢复为 `gtd`，description 恢复原 gtd 的原文，正文中的 `gtd-ollo-test` 改成 `gtd`、去掉字面“仅供测试”标签；业务正文不重写。原 gtd 的说明语义因而保留。若测试正文还有其他测试限定或旧平台指令，设计会话必须先审阅并整理，不能仅凭工具的 A7 关键词检查就认定语义正确。工具拒绝正文写死 `Ollo 测试·*`。原 Skill 不复制进这个公开仓库。

## 时间窗、日账和重启

建议选 **10:15–20:15** 的空闲窗口，预留 15–30 分钟。不靠“任务应该早就结束”判断：确认无报告生成、确认写入、随手记、wake、重试、离线手机待发确认或其他写入；让用户暂时关闭 Ollo App 和 Hermes 桌面 App。工具额外禁止在 08:00、09:30、21:00 前后各 30 分钟执行 apply，并检查八个 next_run_at 都在半小时以后；过期调度或挂起执行先处理，不能靠切换抹掉。

今日安排日账按 **binding + business_date** 每天一份，不按任务 ID。若切换日已经由测试任务生成了今日安排，真实 ID 当天再次生成的报告仍可能被抑制；换 ID、重启服务都不会清账。当天继续看到测试报告是预期，不删报告、不改 binding、不清数据库去“补发”。默认等下一天自然今日安排。工作日当晚收件箱是另一种报告，可先验收；周日回顾仍须单独验证。采集失败的模型说明也可能占用 agenda 日账，这是已有边界，不能把手动重跑当作修复。

代码读取时机：

| 内容 | 代码依据 | 切换含义 |
| --- | --- | --- |
| jobs.json | `cron/jobs.py::load_jobs`；调度 due scan 每次加载，运行中任务及投递队列已有独立快照 | 单独改任务通常不用重启，但不会改掉在途快照 |
| bridge.json（插件） | `ollo-bridge/__init__.py::register` 调 `read_config`，config 保存在 adapter/Binding/Schedule 中 | 必须重新加载；本方案停启 Hermes 一次 |
| bridge.json（脚本） | `collector.py::main` 每次打开配置 | 不可在线先删 apple，避免脚本已读真实名、插件仍用测试映射 |
| skills | `scheduler_prompt._load_cron_skill_parts` 每次任务调用 `skill_view` 读取正文；目录索引有缓存 | 下一次 cron 读取新版；旧对话已注入的上下文不会被改写，不能以旧对话证明新 Skill 生效 |
| Ollo 网关白名单 | 用户提供的设计会话核对：configure-bridge-jobs 后需重启 | 独立服务，Hermes 的重启不能替它加载 |

推荐**协调停机窗口**：排空后停两边；Ollo 配置先写八个 ID（旧四个 + 新四个）；执行本工具；确认没有任何旧在途报告后，Ollo 配置再缩成真实四个；先启动 Ollo、验证，再启动 Hermes。两个白名单文件在停机时完成过渡，期间没有发送者；不靠一个实际未加载的“八 ID 配置”声称服务已经兼容双 ID。

正常预计 Hermes **1 次停启**、Ollo **1 次停启**，各服务只启动一次，合计两个独立服务周期；停机预算 5–15 分钟，不保证固定耗时。若必须让 Ollo 持续在线接收旧报告，则先让八 ID 白名单重启生效，排空和切换后再删测试 ID并第二次重启 Ollo；此备选需要 Hermes 1 次、Ollo 2 次。不能既要求在线过渡，又承诺两边总共只重启一次。若发现旧队列或 unknown 回执未解决，暂停切换，不删白名单。

## 工具与备份

入口：[production_switch.py](../../hermes-plugins/ollo-bridge/scripts/production_switch.py)，同目录依赖 `production_switch_io.py`，还需要本插件的 `docs/`、`apple_config.py`、采集脚本及 collector 源码。用开发副本整套已审阅文件执行即可，不必部署插件代码或更新运行 checkout。

```bash
hermes_script "$SWITCH" plan \
  --accept-prompt-drift '36c04d45e58c=<设计会话填写实际SHA256>' \
  --accept-prompt-drift '4eabdf7d938b=<设计会话填写实际SHA256>'
hermes_script "$SWITCH" apply --backup-dir <新绝对目录> --quiesced \
  --accept-prompt-drift '36c04d45e58c=<设计会话填写实际SHA256>' \
  --accept-prompt-drift '4eabdf7d938b=<设计会话填写实际SHA256>'
hermes_script "$SWITCH" rollback --backup-dir <同一目录> --quiesced
```

上面的 `hermes_script` 是下方完整命令中的 shell 函数，复用当前安装的 `runtime_command(code=...)` 来启动受 PM 管理的 Python，再用 `runpy.run_path` 执行外部脚本。已核对本版本启动器：`hermes --run-module` 只接受模块名，且 Python `-I` 忽略外部 PYTHONPATH；不能用它直接传本开发副本的文件路径，也不能用 `python -m runpy <文件路径>`。不需要为此部署新模块或改运行 checkout。不写子命令等于 plan。显式要求 HERMES_HOME，避免误用默认 Profile。`--quiesced` 表示执行人已经确认两个网关、外部 worker、所有写入者都停止并排空；不是自动停服务的开关。工具检查已记录 gateway PID 是否还活着、投递队列是否还有 pending/delivering/unknown，但这不能替代设计会话核对进程、其他 Profile 的共享网关、手机待发操作及 Ollo 回执。

`plan` 不调用 helper、模型或网关，不读任何提醒事项；不加载插件，不用会自动修复任务文件的 `cron.jobs.load_jobs()`。`create_job` 会生成新 ID，逐条 `update_job` 也不提供跨 jobs/bridge/skills 的事务，所以本工具采用停机下的受锁文件迁移：同一个 `cron/.jobs.lock`、整份 JSON 单文件原子替换、原始字段保留、多文件操作的备份与恢复。没有核心补丁。

工具检查任务字段、测试/真实 ID、候选、Skill 名称、helper 文件及可执行位、脚本和 collector 与当前源码一致、Apple 映射、默认采集范围。只校验 config.yaml 的顶层 `timezone: Asia/Shanghai`（以及冲突的 HERMES_TIMEZONE），不输出其他配置、不修改配置；备份只复制。不会读取或输出 `bridge.key`、`.env`、`auth.json`；不导入会读取 key 的 `config.read_config()`。不接受路径中的软链接、特殊文件或 Skill 目录中的上述秘密文件。

apply 的备份内容：

- `before/cron/jobs.json`：完整八任务及任何无关任务。
- `before/ollo/bridge.json`：完整非密钥配置，保留 session/helper/网关地址等。
- `before/skills/gtd/`、`before/skills/gtd-ollo-test/`：完整原目录，包括配套文件和空目录。
- `before/config.yaml`：仅复制；apply 与 rollback 都不修改它。
- `manifest.json`：所属 home、迁移状态、前后文件长度和哈希、原权限及安装权限；`accepted_prompt_drift` 按实际放行的测试 ID 记录 `current_sha256` 和 `candidate_sha256`，在修改目标前随备份写入，回滚保留此记录。Skill 配套脚本的可执行位保留，回滚恢复原权限。

备份目录及子目录 700、文件 600，写后重读校验完成才修改目标。备份父目录须已存在，指定的备份目录必须是新的，并与目标位于同一文件系统；绝不覆盖旧备份。目标文件的临时写入也放在这个私有备份目录内，再原子移入目标；强杀留下的临时文件不会混入 Skill 或影响回滚比对。备份留在本机，不上传或贴出。`hermes-plus-gtd` 不动，故不纳入恢复。Ollo 网关的原白名单另按其仓库方法保留，不能只依赖 Hermes 备份。

apply 普通异常或中断会尝试立即恢复；进程被强杀、机器断电时保持服务停止，使用 rollback 从 manifest 恢复。任一步失败都不继续启动。在同样停机前提下，重复 apply 在目标完全一致时返回 `apply=noop`，不覆盖首次备份；重复 rollback 也可执行。失败后恢复原状态若要重新 apply，使用新的备份目录。这不是文件系统级跨文件原子事务，停机窗口和可恢复备份是必要条件。

## 设计会话完整命令序列

以下是待执行步骤，**本次未运行**。先在 Ollo 仓库核对 `install.py configure-bridge-jobs --help` 及实现：重复 `--job` 是完整集合替换、配置操作本身不会偷偷启动服务；若语义不同，按实际实现修订命令再请用户同意。Ollo 的解释器、安装器和 LaunchAgent plist 以设计会话已经验证的路径填入，不能猜路径。

### 0. 测试与只读预检

先在开发环境补跑文末测试。下面使用**已安装的 Hermes 入口**，不要在开发激活的 shell 里把 home 改成线上；建议另开 bash。先核对真实清单/日历名称、当前八任务和 Skill 内容、无重复名、helper 权限。

```bash
# 在干净的 bash 中；路径占位由设计会话按实际部署填写。
set -euo pipefail
umask 077
export HERMES_HOME="$HOME/.hermes"
SWITCH="$HOME/Projects/hermes-agent/hermes-plugins/ollo-bridge/scripts/production_switch.py"
HERMES_BIN="$HOME/.local/bin/hermes"
OLLO_PYTHON='<Ollo部署环境Python绝对路径>'
OLLO_INSTALL='<Ollo仓库绝对路径>/server/deploy/install.py'
OLLO_PLIST='<已核实的com.birderyu.ollo.gateway.plist绝对路径>'
SWITCH_BACKUP="$HERMES_HOME/backups/ollo-gtd-production-$(date +%Y%m%d-%H%M%S)"
# 两个占位均由设计会话填写完整 prompt 的实际 SHA-256；不要直接运行占位值。
WEEKDAY_PROMPT_SHA256='<36c04d45e58c当前prompt的实际SHA256>'
WEEKEND_PROMPT_SHA256='<4eabdf7d938b当前prompt的实际SHA256>'
PROMPT_DRIFT=(
  --accept-prompt-drift "36c04d45e58c=$WEEKDAY_PROMPT_SHA256"
  --accept-prompt-drift "4eabdf7d938b=$WEEKEND_PROMPT_SHA256"
)

HERMES_SOURCE="$HERMES_HOME/hermes-agent"  # 本次默认 Profile 的已核实运行源码位置
# 只取当前安装选择的解释器路径；不硬编码某一代 PM 环境。
SWITCH_PYTHON=$("$HERMES_BIN" --print-runtime-command | \
  /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)[0])')
hermes_script() {
  "$SWITCH_PYTHON" -I - "$HERMES_SOURCE" "$@" <<'PY'
import os
from pathlib import Path
import sys
root = Path(sys.argv[1])
args = sys.argv[2:]
sys.path.insert(0, str(root))
from hermes_cli._launchers import runtime_command
code = "import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
command = runtime_command(root, args, code=code)
os.execv(command[0], command)
PY
}

hermes_script "$SWITCH" plan "${PROMPT_DRIFT[@]}"
"$OLLO_PYTHON" "$OLLO_INSTALL" verify
```

验证：plan 返回 0，两条今日安排显示 `accepted_drift: true` 且两边哈希符合已审阅内容；收件箱、每周回顾与候选相同且没有放行记录。其余差异只涉及上文目标字段、测试任务和两个 gtd 目录。不打印整个 jobs.json、bridge.json、配置、Skill 正文或采集输出。Ollo verify 的输出只记录能力/连通性结论，排除凭据与会话内容。plan 不能检查 Ollo 网关白名单，由设计会话在另仓库核对。

**同意节点 A：** 用户审阅差异和 Skill，包括两条今日安排按指定哈希放行并采用候选 A7 规则；接受删除测试任务/Skill、切换当天日账影响、现有采集失败/跨日限制、真实写入边界及回滚方式。

### 1. 进入窗口、停止并排空

**同意节点 B：** 用户同意当前维护窗口及两边各一次停启，暂不操作 Ollo。设计会话先确认活动 runs、确认/capture/离线待发队列、cron fire/run claim、投递 pending/delivering/unknown 都已处理，再停服务；不能通过删数据库或清记录“排空”。

```bash
"$HERMES_BIN" gateway stop
launchctl bootout "gui/$(id -u)/com.birderyu.ollo.gateway"
# 分别确认服务已退出、无外部 worker/其他宿主继续访问该 Profile。
# 此时再 plan 一次；有挂起 claim/manual_run_at 就停下分析，不擅自清字段。
hermes_script "$SWITCH" plan "${PROMPT_DRIFT[@]}"
```

服务原本未加载、bootout 失败或仍有进程时，不要靠忽略退出码继续。停 Hermes 后也可能仍有 detached worker，必须确认已结束。

### 2. 白名单扩容、备份并切换 Hermes

**同意节点 C：** 用户同意这次写入和备份目录。Ollo 原白名单为四个测试 ID，按另仓库既定方式备份配置；这里只展示非密钥的任务参数。

```bash
# Ollo 网关步骤：install.py configure-bridge-jobs --job <真实ID>=<kind>
# 先保存并集；此时两个服务都已停止，不把它误当成已加载。
"$OLLO_PYTHON" "$OLLO_INSTALL" configure-bridge-jobs \
  --job 36c04d45e58c=agenda --job 4eabdf7d938b=agenda \
  --job 188ed50c0816=inbox --job ec742022406f=review \
  --job d83281b75902=agenda --job 8211bb91402a=agenda \
  --job 12d6987e974e=inbox --job f9a5dd70ea34=review

mkdir -p "$HERMES_HOME/backups"
hermes_script "$SWITCH" apply \
  --backup-dir "$SWITCH_BACKUP" --quiesced "${PROMPT_DRIFT[@]}"
hermes_script "$SWITCH" plan "${PROMPT_DRIFT[@]}"
```

验证：`apply=verified`、plan 为 `state=production; changes=0`；manifest 状态 applied、权限正确，`accepted_prompt_drift` 仅含两个已审阅的今日安排测试 ID 及对应哈希。无关任务和 config.yaml/Skill 均不被改动，原任务 model/cron/计数仍保留。不要用重复 apply 代替检查恢复点。任何失败都留在停机状态，先恢复或解决原因。

### 3. 收紧 Ollo 白名单，启动验证

再次确认没有测试报告待发送、没有未完成或 unknown 的测试确认。已有测试卡片之后不再确认；它们留在历史里，并不代表可以继续对真实清单写入。投递白名单不等于历史确认撤权：设计会话须在 Ollo 侧核对旧测试报告新确认如何拒绝或隔离，尤其是会创建事项的建议；未确定之前不要开放真实卡片写入，也不要靠删除提醒事项或数据库处理旧授权。

```bash
"$OLLO_PYTHON" "$OLLO_INSTALL" configure-bridge-jobs \
  --job d83281b75902=agenda --job 8211bb91402a=agenda \
  --job 12d6987e974e=inbox --job f9a5dd70ea34=review
launchctl bootstrap "gui/$(id -u)" "$OLLO_PLIST"
"$HERMES_BIN" gateway start
"$OLLO_PYTHON" "$OLLO_INSTALL" verify
hermes_script "$SWITCH" plan "${PROMPT_DRIFT[@]}"
```

Ollo 启动后先核对其本地进程/监听和已加载白名单为真实四个，再启动 Hermes；完整 `verify` 如依赖 Hermes 连通，放在两边启动后。检查 Hermes 常驻进程、ollo connected、当前进程加载了新 bridge 配置，以及只保留四个正式任务、下一次时间正常。Ollo verify/connected 只证明服务入口，不能代替下一次报告和 Apple 验证。日志只摘错误类型、任务 ID、状态、计数，不打印事项正文或 key。

### 4. 首次业务验证

默认等待下一次自然运行；记下真实任务 ID、execution_id、类型、投递结果、Ollo 卡片/推送是否出现、正文与卡片计数是否相符。今日安排须等未被测试占用的新业务日；工作日 inbox、周末 agenda、周日 review 各有自己的时刻，不把工作日一次成功当成四种都已验证。

**同意节点 D（可选）：** 用户需要提前验证时，仅触发一次经 Hermes 网关调度的运行。今日安排可在 Ollo 使用已验收的“我醒了”入口，但受当日日账约束；inbox/review 可在 Hermes 环境用 `cron.jobs.trigger_job(<真实ID>)` 排入下一 tick（返回非空任务记录后等待网关执行），不要使用 `hermes cron run`。后一命令会在调用进程当场执行并投递，无法验证常驻 osascript 的日历权限。

```bash
# 可选，仅在单独获准、plan 已通过、Hermes 已启动后：示例触发一次收件箱。
# 在私有临时目录创建一个只含固定任务 ID 的短入口，无事项或密钥。
MANUAL_DIR=$(mktemp -d "${TMPDIR:?请先设置系统临时目录}/ollo-gtd-manual.XXXXXX")
cat > "$MANUAL_DIR/ollo_gtd_trigger.py" <<'PY'
from cron.jobs import trigger_job
result = trigger_job('12d6987e974e')
if result is None:
    raise SystemExit('job not queued')
print('queued: 12d6987e974e; wait for the resident gateway')
PY
hermes_script "$MANUAL_DIR/ollo_gtd_trigger.py"
```

这次手动排队会真实投递；不会取消原来的固定时刻，inbox/review 到时仍可能再发一份。脚本采集和报告模型只读，不会创建、修改、移动或删除真实提醒事项。inbox/review 报告建议的写入只在用户点卡片确认后发生，执行人不能替用户点击；A7 选择今日重点只写网关，不改 Apple。注意普通对话中用户直接请求并明确确认后的 Apple Skill 操作、以及“随手记”入口另有写入流程，不能把“采集只读”理解成整个 Ollo 不会写。首次真实卡片写入如需验证，再由用户单独同意一个具体变更，并核对实际 Apple 结果和持久回执；unknown 不换 UUID 重试。

## 回滚

**同意节点 R：** 说明原因及是否已有真实运行/写入，用户同意后操作。最简单的恢复窗口是在首次自然运行之前。停止新请求、排空两边并停服务，和切换时相同；保留 Ollo 数据库/报告/确认回执，不能恢复旧库抹掉已经发生的写入。

```bash
"$HERMES_BIN" gateway stop
launchctl bootout "gui/$(id -u)/com.birderyu.ollo.gateway"
hermes_script "$SWITCH" rollback \
  --backup-dir "$SWITCH_BACKUP" --quiesced
"$OLLO_PYTHON" "$OLLO_INSTALL" configure-bridge-jobs \
  --job 36c04d45e58c=agenda --job 4eabdf7d938b=agenda \
  --job 188ed50c0816=inbox --job ec742022406f=review
hermes_script "$SWITCH" plan "${PROMPT_DRIFT[@]}"
launchctl bootstrap "gui/$(id -u)" "$OLLO_PLIST"
"$HERMES_BIN" gateway start
"$OLLO_PYTHON" "$OLLO_INSTALL" verify
```

恢复的是完整 jobs.json、bridge.json 和两个 gtd 目录，配置仍保持原样；真实任务会恢复投 Photon，四个测试任务会恢复 enabled，因此这项影响也须告知用户。rollback 后 plan 应再次显示原来待切换的差异，绝不是 changes=0。启动前还要确认恢复的 next_run_at 没有过期、没有跨过 08:00/09:30/21:00；过期则停止启动，重新审阅调度恢复，避免补跑旧时刻。它不会撤销已经发生的 Apple 写入，也不会删除正式报告。

若首次运行后 jobs.json 被调度器更新、有人改了 Skill 或备份不完整/哈希不符，工具会以 `rollback_drift_detected` / `backup_hash_mismatch` 拒绝覆盖，不提供强制开关。设计会话先保存新状态，在用户同意后按任务字段做定向恢复并保留新的执行记录/next_run_at，不能手工整份覆盖使旧时刻再次运行。该种延后回滚需要重新审阅，不能承诺本工具自动处理所有线上漂移。

## 本次检查与执行前必跑测试

新增 `test_production_switch.py`，使用临时 HERMES_HOME 和合成八任务/bridge/Skill；覆盖默认 plan 无写入和脱敏、apply、幂等、完整回滚、部分失败/进程中断恢复、配置漂移、缺候选/Skill/helper、权限与软链接、备份篡改、后来编辑、时区及运行窗口。既有 bridge 测试保留。

本次补充的 prompt 放行测试覆盖：两个 ID 分别授权、完整 UTF-8 哈希（含首尾空白）、未授权/错误哈希停止、plan 后内容变化时 apply 停止、其他字段仍检查、正式安装候选、manifest 记录与失败恢复/幂等/回滚保留记录，以及两种 CLI 启动入口透传重复参数。

初版准备时实际尝试：

- 按维护文档用 bash，HERMES_HOME/HERMES_RUNTIME_DIR 都指向新临时目录，`source ./activate` 在下载固定 uv 时因沙箱无法连接本地代理失败，正式测试命令未到达。
- 已有开发缓存的 Python/pytest 可启动。原测试 runner 启动时会探测 `$HOME/.hermes/pytest_live_guard.py`，与本次禁止访问运行 home 冲突；只在临时 runner 副本中去掉这段探测，并固定其 scripts 路径，保留环境清洗、逐文件隔离等逻辑，源码 runner 未改。
- 用该临时副本先跑新测试文件，再跑整个 bridge：全部在测试子进程启动时 `PermissionError(1, 'Operation not permitted')`，**14 个文件、0 collected，不能记为通过**。没有改用裸 pytest 绕过 runner。
- Python AST 检查、CLI `--help`、八段 bash 命令的语法、相对文档链接和 `git diff --check` 通过。`python scripts/check --base HEAD` 的 code health 为 0 blocking/0 advisory；11 项检查中只剩原有 `custom/records/2026-08-30-v0.20.6.md:27` 的 no-tmp 文档规则失败，该历史文件本次未改。未做 Apple/helper 调用或真实服务验收。

设计会话随后报告：沙箱外完整 bridge 测试 **14 个文件、335 项通过**，包含切换工具；这是增加 `--accept-prompt-drift` **之前**的结果。本次补丁后已通过上述离线 runner 副本重试单个切换测试文件和全部 bridge 测试，仍在子进程启动时报 `PermissionError(1, 'Operation not permitted')`，分别 1 / 14 个文件、0 collected，不能把先前 335 项作为本补丁通过的证据。本次 Python AST、CLI 帮助、八段 bash 语法检查通过；`scripts/check --base HEAD` 仍仅有上述未改历史文档的 no-tmp 失败，code health 无阻断或提示。新增用例需设计会话在沙箱外重跑。

设计会话执行切换前，在沙箱外、独立开发环境重跑（不得指向线上 home）：

```bash
cd ~/Projects/hermes-agent
# bash；复用隔离开发工具缓存，home 是本次临时目录。
export HERMES_RUNTIME_DIR="$HOME/.cache/hermes-agent-dev/tools"
export HERMES_HOME="$(mktemp -d "${TMPDIR:?请先设置系统临时目录}/ollo-switch-test-home.XXXXXX")"
source ./activate
scripts/run_tests.sh -j 2 hermes-plugins/ollo-bridge/test_production_switch.py --file-retries 0
scripts/run_tests.sh -j 2 hermes-plugins/ollo-bridge/ --file-retries 0
python scripts/check --base HEAD
```

所有失败都先处理，跳过逐项记录。沙箱外 runner 自带 live guard 探测属于其既有行为；本次未执行该探测，若下一会话仍要求绝对不访问运行 home，也应先采用经过审阅的离线 runner 方式。

## 留给后续

Photon 平台以后是否关闭另行决定，不在本次改 config.yaml；测试清单和日历由用户自己删除，建议待正式验证稳定后处理。Ollo 已有测试报告保留在对话历史中，不再用它们提交确认。每周回顾仍待周日 21:00 自然验收或一次明确授权的手动排队。切换完成后由设计会话另记实际时间、验证结果和重启次数，不把本文“待执行”直接改成未经验证的成功记录。

## 执行记录（2026-10-14 11:36–11:39，设计会话执行）

用户在 Ollo 会话中回复「现在开始吧」同意执行。执行前沙箱外跑 `hermes-plugins/ollo-bridge/` 全部测试：14 个文件、357 项通过（含 `test_production_switch.py`）。

| 时间 | 动作 | 结果 |
| --- | --- | --- |
| 11:36 | 预检 | 最后一次对话任务 11:33:51 结束；Ollo 网关无未完成确认；2 条 10-12 13:32/13:34 的 wake 操作停在 prepared（时区修复前失败的「我醒了」，不涉及 Apple 写入）；Hermes 桌面 App 未运行；真实清单（收件箱/工作/生活/家庭/等待/长期）与日历（工作/生活/家庭）只读核对名称存在且唯一 |
| 11:36 | 准备 Ollo C17 版本 | release `20261014T033635998375Z-79944149395f-dirty`（含 C17 停用报告隔离，无迁移，SQLite v9） |
| 11:36:56 | `hermes gateway stop`、Ollo `bootout` | 8642、8765 均无监听 |
| 11:37 | 备份 | Ollo：`~/.ollo-gateway/backups/gtd-prod-20261014T033715Z`（previous.plist、gateway-v9.sqlite3，完整性 ok）；技能：`~/.hermes/backups/pre-gtd-prod-skills-20261014T033715Z`（hermes-plus-gtd、gtd-ollo-test 原件） |
| 11:37 | 两份技能补 A7 | gtd-ollo-test、hermes-plus-gtd 换成补了 A7（今日重点只由网关记住、卡片确认只限收件箱整理与每周回顾）的版本；改动在 Codex 私有副本完成，设计会话逐行审阅，线上原件与审阅原件逐字节一致；正文不进仓库 |
| 11:37 | `plan`（放行两个今日安排测试 prompt 的 A7 段落差异，哈希 7b35b8f1… / 9ab0ee14…） | `state=ready` |
| 11:37 | Ollo 白名单扩为 8 个 ID | 停机中写入 |
| 11:38 | `apply --quiesced` | `apply=verified`，备份 `~/.hermes/backups/ollo-gtd-production-20261014-113809`；随后 `plan`：`state=production; changes=0` |
| 11:38 | Ollo 白名单收紧为 4 个真实 ID，安装 C17 plist 并 bootstrap | 一次成功，C17 版本运行 |
| 11:38:50 | `hermes gateway start` | ollo-bridge 已挂接；photon、hermes_plus、ollo、api_server connected |
| 11:39 | Ollo `verify`（本机、公网） | 五项能力 true，`push_status=ready`，对话与历史通过 |

结果：4 个真实任务 `deliver=ollo:main`、`skills=[gtd, hermes-plus-gtd]`、对应采集脚本，名称、cron、model、next_run_at 不变；4 个测试任务与 gtd-ollo-test 已删除（在备份中）；bridge 无 apple 对应表。停机约 2 分钟，Hermes、Ollo 各停启一次。

待验证：今晚 21:00 收件箱整理（首次真实数据）、10-15 08:00 今日安排、10-18 21:00 每周回顾。测试清单与日历由用户自行删除；photon 平台是否关闭另行决定。
