# 2026-08-24 同步官方开发主线，并改为只跟正式发行版

## 同步（历史做法，已停用）

把官方 `upstream/main` 新增的 155 个提交快进到 `main`，再把 `custom` 上 14 个补丁变基到新基线：

1. 刷新 `origin`、`upstream`；建同步前回退分支 `backup/main-pre-upstream-20260824`、`backup/custom-pre-upstream-20260824`。
2. `main` 以 `--ff-only` 快进到 `upstream/main`；`custom` 变基。
3. `git range-diff` 对比，14 项全部内容等价（`=`）。
4. 推送前二次刷新发现官方又多了 21 个提交，于是没有推中间版本，继续快进、再变基、重测，最后确认基线不再变化才推送。
5. `main` 普通推送，`custom` 用带精确旧 SHA 的 `--force-with-lease` 推送；重启网关并核对运行 SHA、launchd 和 Photon。

结果：`main` = `upstream/main` = `fe483de4d3`；`custom` = `721d6dbc29`。Photon sidecar 12 项、位置解析 8 项、网关与 Photon Python 154 项通过；官方新增提交没有改依赖，所以没有重装。网关运行新提交，Photon 已连接；这次没有额外发真机测试消息。

## 为什么改成只跟正式发行版

复核发现 `upstream/main` 是高频开发主线，一次刷新就会多出大量细粒度修复和维护提交，持续追平会不断增加变基与回归风险。当天起：

- 开发主线只观察，常规升级只以官方正式发行标签为目标。
- 只有标签包含当前接受基线时，才把 `main` 快进到标签，再变基、测试、部署 `custom`。
- 当时最新标签 `v2026.8.19` 早于基线 `fe483de4d3`，所以不降级，等下一个标签。

现行流程见 [维护方式](../maintenance.md#发行版升级)。
