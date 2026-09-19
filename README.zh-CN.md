# Ollama Translator

[![CI](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml/badge.svg)](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) | **简体中文**

一个完全本地运行的整本书翻译流水线，支持英译中与中译英。输入 EPUB 或 RTF，输出
排版完整的 EPUB。全程只调用本地 Ollama 模型：不依赖云端 API，不需要 MCP 服务，
也不引入 LangGraph、AutoGen 之类的 Agent 框架。

## 它解决什么问题

整本书的翻译，失败方式和单次调用完全不同。用大模型直接逐章翻译，通常会遇到：

- **术语漂移**：同一个人名、地名在第三章和第二十章被译成两个样子；
- **结构损坏**：`<em>`、`<i>` 等行内标签在改写中丢失，EPUB 打开后排版错乱；
- **数量失真**：数字、单位、距离、时间被悄悄改掉，且难以人工察觉；
- **越修越糟**：模型"修复"了一个本来正确的句子，把好译文换成了差译文；
- **中断即重来**：跑到第 40 章报错，只能从头再来一遍。

这个项目把上面每一类问题拆成独立的、可断点续跑的阶段来处理，而不是寄希望于
一个更长的提示词。

## 工作流程

```text
decompile
  -> extract_glossary -> resolve_glossary -> approve_glossary
  -> preprocess -> translate
  -> audit_translation -> repair_translation -> reprose_translation
  -> review_repaired -> repair_review -> validate_repaired
  -> compile -> validate_epub
```

共 14 个阶段，每个阶段都把产物写入 `runs/<job-id>/` 下的独立工作区。执行
`resume` 时，已完成并通过校验的阶段会被跳过，任务从最早受影响的阶段继续，
而不是从头开始。

## 示例：《爱丽丝梦游仙境》

仓库自带的古登堡计划样书可以完整跑通英译中流程。以下步骤都在本地浏览器控制台中
完成：

```powershell
book-agent ui          # http://127.0.0.1:8765/
```

**1. 配置与启动。** 任务页分页列出全部任务（每页 10 个）。点击 *Add new job*
只需选择源书（从已知文件中选择、浏览或直接拖入）、填写配置文件名，任务 ID 默认
取配置文件名；已存在的配置（如 [`configs/demo-alice.yaml`](configs/demo-alice.yaml)）
会直接沿用，新文件名则以 `config.example.yaml` 为模板创建。随后进入任务的
**Config** 标签页：*Options* 汇总最常改动的设置（译文风格、各角色模型及其安装
状态 ✓/✗、术语表审批方式、质量检查、输出选项）；*All settings* 列出全部配置项，
附类型、取值范围、默认值与重置按钮；*YAML* 可直接编辑文件，三者实时同步。
*Validate* 会保存并校验配置、试运行，并确认所需模型均已安装；只有配置自校验
通过后未再改动，任务顶栏的 *Start translation* 才可用。运行期间顶栏提供
*Pause*（等当前模型调用结束后暂停）与 *Stop*（立即停止），之后可用 *Resume*
从最近的检查点继续。示例配置只使用本地
已安装的模型，启用了译文润色与 EPUB 清理，并由模型审批术语表。在终端中运行
同一任务可使用 `.\scripts\demo-alice.ps1`（`-Resume` 用于续跑）。

**2. 术语表审批。** 开启 `workflow.llm_glossary_review: true` 时，有证据支撑、
置信度高且无冲突的条目直接通过，其余交由模型复核；术语表页面会展示最终术语、
哪些条目经过模型复核、原因以及改动。若采用人工审批，流程在此暂停，同一页面
变为编辑器：可修改译名、类别、备注与别名，拒绝条目，筛选需要关注的条目
（泛化词、译名冲突、低置信度、无证据），并查看每个术语的原文例句。可以直接
批准自己的版本，也可以把它（或未改动的草稿）交给模型复核，随后流程自动继续。

**3. 翻译、审校与修复。** 进度页实时跟踪任务，无论任务由控制台还是终端启动：
展示每个阶段的状态、计划的模型任务数、当前单元、调用次数、输出 token 与耗时，
以及正在进行的模型调用和会话日志。本书在编译前暂停，留下三个流水线无法自行
确认的段落。

**4. 最终人工审校。** 在 Final review 页面（也可用
`book-agent review-ui .\runs\demo-alice-en-zh` 直接打开）处理待复核段落。每个段落都会展示原文及上下文、审校发现（点击即可高亮引用
片段）、流水线各版本译文，以及带实时差异对比的编辑器；编辑器会执行与
`resolve-review` 相同的确定性校验：

![审校台处理待复核段落](assets/demo-final-review-desk-processing.png)

提交决定后草稿即获批准，审校台随后编译并校验 EPUB：

![审校台提交并完成编译](assets/demo-final-review-desk-results.png)

## 设计取舍

- **可机械校验的结构，交给程序而不是模型**：EPUB 结构、行内标记、各类标识符
  全部由确定性代码掌控，不交给模型自由发挥。
- **宁可弃权，也不做没把握的修复**：无法通过校验的修复会被丢弃，不允许覆盖
  已有的较好译文，该段落转入人工复核。
- **重试有上限**：当校验结果不再收敛时停止继续调用模型，避免在同一个失败上
  空转。
- **只做通用处理**：所有失败类型都按通用规则处理，代码中不写死针对某本书、
  某个段落的补丁。
- **上下文按需分配**：配置里的 131K 是上限而非每次都申请的额度。普通请求从
  16K 起步，定点修复与修复校验从 8K 起步，按 16K / 32K / 64K / 131K 逐级增长，
  让显存占用与请求规模成正比。

## 快速开始

### 1. 准备 Ollama 与模型

先确保本地 Ollama 已在运行，并拉取配置中用到的模型：

```powershell
ollama pull qwen3.8:latest
ollama pull gemma4:31b
```

首个需要调用模型的阶段开始前，CLI 会先校验模型名称与配置的上下文容量。

### 2. 安装

```powershell
python -m pip install -e .
book-agent --version
```

也可以使用 requirements 文件安装（`pyproject.toml` 中的依赖声明为准）：

```powershell
python -m pip install -r requirements.txt
python -m book_agent --version
```

需要 Python 3.11 以上，以及 `ollama>=0.6.2`。

### 3. 复制并检查配置

```powershell
copy config.example.yaml my-book.yaml
book-agent config --file .\my-book.yaml
```

`config.example.yaml` 是一份偏保守的生产起始配置，并非默认值的罗列；其中若干
取值刻意不同于代码默认值。所有键都可以省略，省略即采用 `book_agent/config.py`
中的默认值。

### 4. 先试运行，再正式开跑

试运行不写任何文件，也不连接 Ollama：

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --dry-run
```

确认无误后正式执行：

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --job-id "my-book-en-zh"
```

命令会打印工作区路径，请记下来，后续所有命令都针对该目录操作。RTF 输入使用
同一条命令与同一条流水线。

### 5. 查看状态与续跑

```powershell
book-agent status .\runs\my-book-en-zh
book-agent resume .\runs\my-book-en-zh --plain
```

`--plain` 输出适合写入日志，不改变流水线行为。

## 模型角色分工

默认分工偏保守，各阶段可独立指定模型：

| 任务 | 默认模型 |
|---|---|
| 术语表候选抽取 | `qwen3.8:27b` |
| 术语消解与审核 | `qwen3.8:latest` |
| 初译与定点修复 | `qwen3.8:latest` |
| 语义审计 | `gemma4:31b` |
| 数量校验与裁决 | `gemma4:26b`，必要时升级到 `gemma4:31b` |
| 修复比对与有界校验 | `gemma4:26b` |
| 文风重写提案 | `qwen3.8:latest` |
| 文风重写校验 | `gemma4:31b` |

用于结构化 JSON 的审计与校验调用默认关闭 thinking；除非有实测的质量收益，
否则不建议打开。

## 审校与续跑

默认 `require_glossary_review: true`，即生成术语表草稿后暂停，等待人工确认：

```powershell
book-agent approve "D:\runs\my-job" --glossary "D:\reviews\glossary.txt" --resume
```

也可以改用受 schema 约束的模型复核并立即继续：

```powershell
book-agent approve "D:\runs\my-job" --llm-glossary --resume
```

复核方可以修正或删除条目，但不能凭空新增英文词条，并会记录提示词/模型哈希、
尝试次数、复核模式与产物。

### 浏览器控制台

`book-agent ui` 启动只监听本机地址的控制台（可选 `--runs`、`--configs`、
`--port`、`--no-browser`），覆盖与上述命令相同的关卡：

- **Jobs**：列出并新建任务，并显示翻译方向（如 `EN → ZH`）；已完成的任务可在
  列表或任务顶栏**下载**译好的书；任务的 **Config** 标签页负责编辑、校验与启动，
  顶栏可暂停（当前模型调用结束后）、停止或续跑。终端启动的任务可用
  `book-agent pause <workspace>` 以同样方式暂停；
- **Progress**：根据 `state.sqlite3` 与会话日志跟踪 `--runs` 下的任意任务。
  流水线每一行：失败或暂停的阶段可**续跑**（保留已完成的工作）或**重跑**；
  已完成的阶段可**从此处重跑**。重跑（即 `retry --stage X --resume`）执行前
  会弹窗列出需要重做的阶段及将丢失的内容；
- **Glossary**：编辑并批准暂停中的术语表，或交给模型复核
  （对应 `approve --glossary` / `--llm-glossary`）；
- **Final review**：以与 `resolve-review` 相同的校验处理人工复核队列；接受
  当前译文前必须选择预设理由或填写自定义理由。未决片段数降到
  `workflow.compile_max_unresolved_review_segments` 以内时，会询问继续处理，
  还是先应用已决定的片段并批准终稿。`book-agent review-ui <workspace>`
  可直接打开此页面。

控制台发起的操作都以子进程方式调用常规命令行，因此日志与检查点与终端运行
完全一致，关闭控制台后任务仍会继续。

控制台前端位于 `frontend/`（React + TypeScript），构建产物已提交到
`book_agent/web/static/`，安装本包无需 Node.js。修改界面时（Node 24）：

```powershell
cd frontend
npm ci
npm run dev      # 热更新开发；/api 代理到正在运行的 `book-agent ui`
npm test
npm run build    # 类型检查并重新生成 book_agent/web/static，请一并提交
```

## 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 命令成功，或流程正常结束 |
| `1` | 校验、配置、模型、阶段或运行失败 |
| `2` | 流程在审校关卡处暂停 |
| `130` | 用户取消，或模型生成被协作式中断 |

## 测试

测试套件默认离线，不需要本地模型即可运行：

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

## 更多文档

完整的命令参考、逐项配置说明与运维细节目前以英文维护：

- [English README](README.md)
- [生产运维指南](docs/OPERATIONS.md)
- [架构设计](docs/DESIGN.md)
- [整书一致性方案（英文，尚未实现）](docs/BOOK_CONSISTENCY.md)
- [控制台界面本地化方案（英文，尚未实现）](docs/LOCALIZATION.md)
- [控制台阶段控制：续跑、重跑与提前批准（英文）](docs/STAGE_CONTROL.md)
- [实施记录](docs/PLAN.md)
- [推理框架基准测试方案（尚未执行）](docs/FRAMEWORK_BENCHMARK_PLAN.md)

## 许可证

代码与文档采用 [MIT 许可证](LICENSE)。`sample/` 目录下的 EPUB 来自古腾堡计划，
保留其自带条款，不在 MIT 授权范围内，详见 [sample/README.md](sample/README.md)。
Ollama 模型不由本仓库分发，各自遵循其模型许可证。
