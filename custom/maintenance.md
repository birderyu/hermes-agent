# 维护方式

本 fork 怎么改、怎么跟官方升级、怎么验证。部署到 Mac mini 见 [部署](deploy.md)；当前补丁见 [补丁清单](patches.md)。

## 三个位置

| 位置 | 用途 | 规则 |
| --- | --- | --- |
| 开发副本 `~/Projects/hermes-agent` | 所有修改、合并上游、测试、提交、推送 | 远端 `origin`（本 fork，HTTPS）与 `upstream`（官方）；依赖环境与运行环境隔离，见下文 |
| fork `github.com/birderyu/hermes-agent` | `main` 与 `custom` 的唯一来源 | 公开仓库：不提交凭据、电话号码、Photon 项目标识、允许名单、会话内容和实际位置 |
| 运行目录 `~/.hermes/hermes-agent` | Mac mini 上实际运行的代码 | 只部署、不开发；只能从 `origin/custom` 更新；只有 `origin` 远端，见 [部署](deploy.md) |

2026-10-11 之前 `~/Projects/hermes-agent` 是指向运行目录的软链，改代码就等于改线上；之后改为独立 clone。

GitHub 的 SSH 22 端口在本机网络下经常被关闭，开发副本的 `origin` 用 HTTPS。

## 分支

```text
官方正式发行标签（如 v0.21.6）
       │ 确认包含当前 main 后快进
       ▼
main      只放官方基线，不提交个人修改
       │ rebase
       ▼
custom    main + 个人补丁 = Mac mini 实际运行的版本

upstream/main   官方开发主线，只观察，不作为升级目标
backup/*        每次升级前的本地回退分支，不推送
```

## 开发环境

v0.21.6 起 Hermes 的依赖由内置的 `pm` 管理（`setup-hermes.sh`、`./activate`、`hermes pm`），默认把工具装进 `~/.hermes/tools`、把环境装进 `$HERMES_HOME/installs/`，和运行目录共用。开发副本必须隔离，固定用下面两个变量：

```bash
export HERMES_RUNTIME_DIR="$HOME/.cache/hermes-agent-dev/tools"
export HERMES_HOME="$HOME/.cache/hermes-agent-dev/home"
cd ~/Projects/hermes-agent
bash setup-hermes.sh --runtime-only --test-environment   # 首次或依赖变化后
source ./activate                                         # 进入开发环境（hermes 命令只在本目录生效）
```

- 只用 `--runtime-only`。不带它的 `setup-hermes.sh` 会改写 `~/.local/bin/hermes`（运行中的 Hermes 入口）、向 `$HERMES_HOME/skills` 同步技能、改 shell 配置。
- 不要用裸 `pip`/`uv` 改 Hermes 的环境（仓库 `pm/AGENTS.md` 的规定）。
- 测试一律 `scripts/run_tests.sh`，不用裸 `pytest`。不激活时可以用 `HERMES_PYTHON=<测试环境的 python> scripts/run_tests.sh …`；测试环境的 python 用 `source ./activate` 后的 `$__HERMES_TEST_PYTHON` 查到。
- `source ./activate` 要在 bash 里执行。zsh 下 `scripts/_activation.sh` 查找 Windows 版 Python 的通配符（`python-*/python.exe`）匹配不到会报 `no matches found`，激活失败。用 `bash -c 'export HERMES_RUNTIME_DIR=… HERMES_HOME=…; source ./activate; scripts/run_tests.sh …'`，或者先进入 bash。
- `source ./activate` 每次都会先按锁文件同步一遍环境（已是最新时很快），所以依赖变化后直接重新激活即可。
- 用 Codex CLI 改代码时，它的 `workspace-write` 沙箱默认禁止写 `.git`，需要单独放开开发副本的 `.git`：`-c 'sandbox_workspace_write.writable_roots=["/Users/birderyu/Projects/hermes-agent/.git"]'`，`~/.hermes` 保持不可写。沙箱里跑不了 Hermes 的 Python 测试（测试进程启动即 `PermissionError`），测试在沙箱外跑。在 git worktree 里变基时，沙箱会拒绝创建 worktree 的 git 状态目录（`.git/worktrees/<名>/rebase-merge`），即使主仓库的 `.git` 在 `writable_roots` 里；这时让 Codex 只做静态审查和冲突合并候选，git 操作在沙箱外执行（2026-10-15）。

## 新增或修改补丁

1. 在开发副本的 `custom` 上改，一个问题一个提交，标题沿用 `fix(photon): …`、`fix(cron): …` 这类格式。
2. 先确认上游有没有修过（看 `upstream/main` 和官方 issue）；能提给上游的通用修复，同时提 PR。
3. 按问题加行为测试，用 `scripts/run_tests.sh` 跑相关目录。
4. 在 [补丁清单](patches.md) 登记：提交、为什么、上游状态、什么时候可以移除。
5. `git push origin custom`（普通快进推送）。部署另走 [部署](deploy.md)。
6. 在 `records/` 写一篇记录，并更新 [记录索引](records/README.md)。

`hermes-plugins/` 下的 Ollo 插件同样在 `custom` 上维护，说明见 [hermes-plugins/README.md](../hermes-plugins/README.md)。

## 发行版升级

只跟官方正式发行版。候选标签要同时满足：

- 是官方 GitHub Releases 上已发布的版本（`gh release list -R NousResearch/hermes-agent`）。`rc.*`、`*+canary.*`、`abandoned-*` 这类标签不算。标签命名在 v0.21.6 由 `vYYYY.M.D` 改成了 `vX.Y.Z`，以 Releases 页为准。
- 当前 `main` 是标签的祖先（`git merge-base --is-ancestor main "$TAG"`）。
- 标签能从 `upstream/main` 到达（`git merge-base --is-ancestor "$TAG" upstream/main`）。
- 读过发行说明，重点看 Photon、网关、API server、插件接口、`pm`/依赖、配置迁移。

标签早于或不包含当前基线就停下，不降级，不强制移动 `main`。

### 步骤（都在开发副本里）

```bash
cd ~/Projects/hermes-agent
git status --short --branch            # 必须干净，custom == origin/custom，main == origin/main
git fetch --prune --tags upstream && git fetch origin
TAG=vX.Y.Z
git merge-base --is-ancestor main "$TAG" && git merge-base --is-ancestor "$TAG" upstream/main

D=$(date +%Y%m%d)
OLD_ORIGIN_CUSTOM=$(git rev-parse origin/custom)
git branch "backup/main-pre-$TAG-$D" main
git branch "backup/custom-pre-$TAG-$D" custom

git switch main && git merge --ff-only "$TAG"
git switch custom && git rebase -i main      # 逐个核对补丁：上游已修好的 drop
git range-diff "backup/main-pre-$TAG-$D..backup/custom-pre-$TAG-$D" main..custom
```

- 变基冲突：先 `git rebase --abort`，保存冲突差异、读懂上游现在的代码，再重放；按补丁原意合并，不把上游新行为改回去。
- 每个 `!` 的 range-diff 都要审阅并写进记录。
- 上游已修好的补丁在这一步 drop，并在 [补丁清单](patches.md) 移到「已移除」。

然后按「开发环境」重新 `setup-hermes.sh --runtime-only --test-environment`，跑测试（下一节）。全部通过后推送：

```bash
git fetch origin
test "$(git rev-parse origin/custom)" = "$OLD_ORIGIN_CUSTOM"   # 远端没被别人动过
git push --atomic --force-with-lease=refs/heads/custom:$OLD_ORIGIN_CUSTOM origin main:main custom:custom
```

只有变基后的 `custom` 用 `--force-with-lease`，并且带精确旧 SHA；不用普通 `--force`。推送后按 [部署](deploy.md) 上线，最后写升级记录。

## 测试与基线对照

1. **定向测试**：`hermes-plugins/`、`tests/cron/` 里与投递相关的文件、`tests/gateway/` 里与补丁相关的文件。
2. **全量测试**：`scripts/run_tests.sh -j 8`，记录文件数、通过、失败、跳过、耗时和首次失败后重试通过的文件。
3. **基线对照**：在一个独立的纯官方标签副本里（`git clone --shared ~/Projects/hermes-agent <临时目录>` 后 checkout 标签，用同一个 `HERMES_RUNTIME_DIR`/`HERMES_HOME` 装环境），用同样的入口复跑全量里所有失败的文件，按测试标识比较：
   - 共同失败：官方本身的失败或本机环境问题，不算定制回归；
   - custom 单侧失败：逐个单独复跑，排除并发竞态后仍失败的才是定制回归，必须修。
4. 结论只写「未发现可复现的 custom 新增回归」，不写「全量全绿」，除非真的全绿。
5. 测试不能写 `~/.hermes`。全量测试生成的未跟踪文件（例如 `MagicMock` 数据库目录）移出仓库，不提交。

常见的共同失败：可选 provider 没装、Linux/systemd/WSL 假设、macOS 权限与桌面驱动、更新器测试对 launchd 的假设、搜索子进程 `ProcessLookupError` 竞态。Mac mini 上开着 Hermes 桌面 App 会让部分测试失败，跑测试前退出。

**全量测试会碰本机正在运行的网关**：服务管理类测试会作用到真实的 LaunchAgent `ai.hermes.gateway`。2026-09-11 和 2026-10-11 都出现过：网关收到 SIGTERM 后由 launchd 拉起，关闭时还会向 iMessage 发一条关机通知。在 Mac mini 上跑全量前要预期这条通知；跑完用 `gateway_state.json` 的 `code_sha` 和进程启动时间确认运行的仍是运行目录的代码。

## 移除补丁

补丁登记的退出条件满足时（通常是上游正式发行版已包含等价修复）：

1. 用补丁自己的行为测试验证上游实现；
2. 在下一次发行版升级的变基里 drop，或在 `custom` 上 `git revert`；
3. 在 [补丁清单](patches.md) 移到「已移除」，写明哪个上游提交取代了它。

## 回滚原则

- 单个补丁出问题：在 `custom` 上 `git revert <提交>`，不改写已推送的历史。
- 变基出问题：`git rebase --abort` 回到变基前。
- 新版本整体有问题：从 `backup/*` 新建临时分支验证；回滚部署见 [部署](deploy.md#回滚)。
- 不删除 `backup/*` 分支，确认新版稳定一段时间后再清理。
