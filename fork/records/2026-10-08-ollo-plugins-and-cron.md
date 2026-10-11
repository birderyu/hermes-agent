# 2026-10-08 Ollo 插件归并与调度补丁入库

## 插件改名与新插件

App 从 Hermes+ 改名为 Ollo，插件一起改名，并新增会话插件：

| 提交 | 内容 |
| --- | --- |
| `6671dad141` | `hermes-plus-inbox` → `ollo-inbox`，`hermes-plus-location` → `ollo-location`；新增 `ollo-conversation`（会话发现接口、自称 Ollo、消息语气标记）；旧接口路径、平台名、配置目录、环境变量保留别名；首次启动把旧数据目录搬到新名字，搬前备份 |
| `e885280c79` | 补记两次部署、语气辅助分类、`mood.py` 与测试结果 |

插件先在独立工作副本的 `ollo-plugins` 分支开发，用户同意后快进合入 `custom`。设计、改名对照和部署清单见 [hermes-plugins/README.md](../../hermes-plugins/README.md)。

部署记录（运行环境，不在本仓库）：15:48 首次部署三个插件，备份在 Hermes home 的 `backups/ollo-deploy-20261008-1525/`；18:49 只更新 `ollo-conversation` 到 1.1.0（回复写完后由辅助任务 `ollo_mood` 分类语气，3 秒超时），备份在 `backups/ollo-mood-deploy-20261008-1848/`。

## 调度补丁（`309821b2e1` fix(cron): pass execution_id to live-adapter delivery metadata）

运行目录里早就有一行未提交的修改：把已有的任务执行 ID 透传到普通 live-adapter 文本投递的元数据里。`ollo-inbox` 用 `execution_id` 给定时报告去重，缺少有效执行 ID 时拒收。这天单独提交入库，内容不变，`.bak` 保留且不提交。

```python
route_metadata = {"job_id": job["id"], "execution_id": job.get("execution_id"), "notify": t.notify_delivery}
```

## 验证

| 范围 | 通过 | 失败 | 跳过 |
| --- | ---: | ---: | ---: |
| 8 个插件测试文件 | 107 | 0 | 3 |
| 2 个调度回归文件 + 2 个临时行为验证文件 | 29 | 0 | 0 |

测试用 `scripts/run_tests.sh -j 2 ... --file-retries 0`，在源码的完整临时副本里跑，只把副本测试器的 scratch 目录换成允许的临时路径；3 项跳过是沙箱禁止监听回环 socket，不算通过。临时验证首次有一个文件导入路径错误，修正临时文件后整组重跑通过；没有为过测试改产品代码。

三个 `ollo-*` 源码与已部署副本 `diff -rq -x __pycache__` 一致。这天只归并源码、提交已有补丁，没有部署、重启或改运行配置。

推送：本机 SSH 被关闭，用 HTTPS 地址推送，没有改保存的远端配置。

## 当时的已知遗留

- 运行目录就是 fork 的工作区（`~/Projects/hermes-agent` 是指向它的软链），通过它编辑就是改运行中的代码。2026-10-11 起改为独立开发副本，运行目录只部署，见 [维护方式](../maintenance.md)。
