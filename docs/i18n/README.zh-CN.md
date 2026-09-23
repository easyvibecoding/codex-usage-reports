<p align="center">
  <img src="../../docs/assets/hero.png" alt="Codex Usage Reports — 每个 Task，每一轮的用量。" width="100%">
</p>

# Codex Usage Reports

**自动生成每个 Codex Task 和每一轮的 Token 用量报告。** 在简洁的本地报告中查看 Task 累计用量、单轮增量、实际观测到的模型、推理强度和子代理用量。

[![CI](https://github.com/easyvibecoding/codex-usage-reports/actions/workflows/ci.yml/badge.svg)](https://github.com/easyvibecoding/codex-usage-reports/actions/workflows/ci.yml)
[![MIT 许可证](https://img.shields.io/badge/license-MIT-mintcream)](../../LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)](../../pyproject.toml)

[English](../../README.md) · [繁體中文](README.zh-TW.md) · **简体中文** · [日本語](README.ja.md)

[快速开始](#快速开始) · [报告示例](#查看报告) · [使用指南](../../docs/USAGE.md) · [数字如何计算](../../docs/METRICS.md) · [故障排查](../../docs/TROUBLESHOOTING.md)

## 自动更新与一次授权

首次安装此版本后，在 CLI 输入 `codex` → `/hooks`，检查并信任固定入口。此后常规运行程序与 CLI 更新沿用相同的 hook 定义，无需重新授权；这表示同意执行同一发布者未来签署的程序。默认在提交提示时启动后台检查，每六小时最多一次，只有签名和文件哈希都通过的版本才会启用。新任务使用新版并显示已验证版本，进行中的任务保留原版。

新增 hook、更改入口或密钥，以及插件／skill 结构更新，仍须更新插件并检查变更的 hooks。工具不会修改 Codex 的信任记录。在已安装插件目录执行 `python3 scripts/publisher_updates.py status` 可读取实际版本；将 `status` 换成 `off`、`on`、`update` 或 `rollback` 可禁用、启用、手动更新或回退。Plugins 页面可能仍显示原安装版本。[更新机制与操作](../SIGNED_UPDATES.md) · [授权提醒](../UPDATE_NOTICES.md)。

## 项目 exec 活动

按工作目录查询额外启动的 `codex exec`、在前台监看变化，或使用可选启动器保留 ephemeral 执行的开始、退出及用量记录。更新并重新信任 hooks、开启新任务后，会在工具返回时提示新活动。启动器归属与原生子代理关系分开，exec 用量不会自动计入主任务。[命令与覆盖范围](../EXEC_ACTIVITY.md)。

## 了解每一轮用了多少

一个长时间运行的 Task 可能包含多轮对话、模型切换和子代理。单个会话的总量无法说明最近一轮的变化。Codex Usage Reports 分别呈现这些范围：

| 想了解什么？ | 报告会显示什么？ |
| --- | --- |
| 这个 Task 累计用了多少？ | 所选父 Task 实际观测到的累计 Token 用量。 |
| 这一轮增加了多少？ | 原生本轮计数，或相同来源的有效累计计数差值。 |
| 使用了哪个模型和推理强度？ | 该轮观测到的设置，包括可见的设置变化。 |
| 子代理用了多少？ | 独立的子代理用量小计，以及数据覆盖状态。 |
| 账号还剩多少配额？ | 有可用数据时，显示原生配额观测值，并与 Task Token 用量分开呈现。 |
| 之后还能查看吗？ | 保存在本地的 HTML、Markdown 和 JSON 报告记录。 |

运行时仅使用 Python 标准库，无需调用 LLM 计算用量、无需 API 密钥，也不会将报告发送到托管分析服务。本项目从 [Codex Run Budget](https://github.com/easyvibecoding/codex-run-budget) 提取报告功能，独立运行。

## 查看报告

![每轮自动报告，显示 Task 累计 Token 和本轮增量](../../docs/examples/turn-en.png)

*此示例使用合成数据，由实际报告模板生成，并置于独立的文档展示框中。桌面版会使用自己的外围主题。*

<details>
<summary>繁体中文示例</summary>

![繁体中文每轮报告示例](../../docs/examples/turn-zh-Hant.png)

</details>

[打开示例集](../../docs/examples/README.md)，下载 HTML、查看完成后的报告记录和指定 Task 报告。品牌插画使用 Codex 图像生成制作；用量截图则由合成测试数据实际渲染。[美术提示词](../../docs/assets/PROMPTS.md)。

## 快速开始

需要 Python 3.10+，以及支持插件 hooks 的本地 Codex 环境。内嵌卡片需要支持本地可视化的桌面界面；CLI 用户可以阅读已保存的报告。原生数据结构因版本而异，请参阅[兼容性与验证](../../docs/VALIDATION.md)。

### 1. 安装插件

```sh
codex plugin marketplace add easyvibecoding/codex-usage-reports
codex plugin add codex-usage-reports@codex-usage-reports
```

在 Codex 中审查并信任插件 hooks，然后**新建一个 Task**。已安装的 hooks 会根据宿主环境的信任和生命周期规则加载。请参阅官方[插件指南](https://learn.chatgpt.com/docs/plugins)和 [hooks 指南](https://learn.chatgpt.com/docs/hooks)。

若更新改变了 hook 定义，请在 Codex CLI 输入 `/hooks`，重新审查并信任插件已变更的 hooks。信任绑定的是确切的 hook 定义：插件即使已安装并启用，状态为 `modified` 的 hooks 仍会被跳过。重启 App 或从手机新建 Task 都不会自动获得信任。完成审查后，再新建一个 Task。

### 2. 照常工作

像平常一样让 Codex 处理工作。Hook 会记录该轮的基准值，要求在最终回答前生成一次卡片，并在收到支持的结束事件时保存报告记录。自动报告默认启用。

`Stop` 之后，一个本地 Python 后台进程会检查该轮的原生 `task_complete` 记录，最多进行八次有范围限制的扫描，重试期限为 25 秒。它不会调用模型，也不会继续 Task。确认该轮的完成边界后，会另存修订报告，纳入已写入的最终回答用量，避免计入下一轮。证据缺失或不完整时，仍保留待更新或部分可用状态；禁用自动报告也会停止后续核对。

内嵌卡片仍是最终回答**之前**截取的快照。原始 Stop JSON、HTML 和 Markdown 报告都会保留；重新查询 Task 报告时，会选用最新发布的修订版。覆盖卡片的 HTML 文件无法可靠地更新原卡片：手机远程 A/B 实验中，重新进入 Task 后，原卡片仍显示 A，新引用才显示 B。因此，本插件不启用原内嵌卡片的自动替换。请参阅[结束后核对与预览行为](../../docs/ARCHITECTURE.md#completion-reconciliation)。

### 3. 查看 Task

使用随附的 `usage-report` skill，例如：

> 显示这个 Task 的用量报告，包括每轮用量和实际观测到的设置。

也可以克隆仓库，使用独立 CLI：

```sh
git clone https://github.com/easyvibecoding/codex-usage-reports.git
cd codex-usage-reports
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report status
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format markdown
```

将 `TASK_ID` 设为要查看的 Task 原生 ID。报告范围仅限于该 Task。HTML 导出、配置和卸载方法，请参阅[完整使用指南](../../docs/USAGE.md)。

## 可以核查的报告

- **如实呈现缺失数据。** 计数器重置、截断记录和设置冲突，都会保留为部分可用或未知。
- **保留历史设置。** 当前的全局模型偏好不会覆盖过去某轮的观测结果。
- **核对完成记录。** 本地后台进程会在有限次数内核对稍后写入的原生记录，并保留原始 Stop JSON／HTML／Markdown 报告和内嵌快照。
- **区分统计范围。** 缓存输入是输入用量的一部分；推理输出是输出用量的一部分。子代理用量和账号配额分别呈现。
- **缓存读取占比。** 卡片和保存的报告会显示缓存输入占已观察输入的比例。缺失或零输入保持不可用；这不是官方的缓存未命中诊断。
- **数据保存在本地。** 报告状态中的原生标识符会经过哈希处理；私人显示名称可能出现在你的本地报告中。
- **轻量 hooks。** 报告出错不会拒绝工具调用或停止代理。
- **多语言卡片。** 支持英语、繁体中文、简体中文、日语、韩语、德语、法语、西班牙语和葡萄牙语；README 提供四种语言版本。

## 从 Codex Run Budget 迁移

两个插件各自独立。如果保留原插件的预算控制功能，请先禁用原插件的自动报告，再启用本插件，以免出现重复卡片。不需要迁移历史数据库。[迁移说明](../../docs/MIGRATION.md)。

## 限制

这些报告记录实际观测到的用量，**不等同于账单或精确费用**。配额属于账号，无法仅凭 Token 总量分摊到单个 Task。子代理用量的归属取决于可获取的原生父子关系数据。Hook 的送达情况和原生数据结构都可能随 Codex 版本变化。本插件不设置预算限制，也不会中断工作。

报告可能透露项目名称和使用模式。请将真实报告保存在本地；公开 issue 仅使用合成测试数据。[隐私与安全](../../SECURITY.md)。

## 参与贡献

请参阅 [CONTRIBUTING.md](../../CONTRIBUTING.md)、[架构](../../docs/ARCHITECTURE.md)和[验证方法](../../docs/VALIDATION.md)。欢迎提交简洁且可复现的错误报告，以及原生数据结构的兼容性修复。请勿附上真实对话记录或数据库。

MIT © EasyVibeCoding contributors。独立社区项目，与 OpenAI 无隶属关系，也未获其背书。[来源与许可说明](../../NOTICE.md)。
