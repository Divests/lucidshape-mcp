# LucidShape MCP

**简体中文** | [English](README.en.md)

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB)](pyproject.toml)
[![Platform Windows](https://img.shields.io/badge/Platform-Windows-0078D4)](docs/installation.md)
[![Transport stdio](https://img.shields.io/badge/MCP-stdio-555555)](docs/harness-compatibility.md)

通过 [Model Context Protocol](https://modelcontextprotocol.io/) 让 AI 助手连接 **CATIA 与 LucidShape**，完成几何导入、模型检查、光线追迹、光度分析和 IES 导出。

[快速开始](#快速开始) · [客户端配置](#客户端配置) · [工具目录](docs/tools.md) · [场景配置](docs/generalization.md) · [常见问题](docs/installation.md#常见问题) · [开发指南](docs/development.md)

## 主要功能

- **CAD 与光学软件连接**：读取 CATIA 零件、导出 STEP/STL，连接现有 LucidStudio 会话并导入几何。
- **仿真与光度分析**：检查已保存的模型、运行 CPU 追迹和参数扫描，比较 LID/IES 并导出 IES。
- **可配置光学场景**：通过 JSON 定义几何区域、材料、光源坐标、点亮工况和探测器；提供边界诊断和明确标注的 GPU 对照。
- **可追溯任务**：保留输入快照、哈希、原生日志及结果校验，支持状态查询、任务列表和取消。

应用操作使用 CATIA COM、LucidStudio DDE 和固定 LucidShell 模板。服务通过本地 stdio 与客户端通信，使用原生接口操作软件。

## 环境要求

- **Windows** 与 **Python 3.11+**；推荐 Python 3.12 64 位。
- 已安装并获得相应许可的 **LucidShape**，以及实际 `lush.exe` 路径。
- 使用 CATIA 工具时，需要已安装并运行 **CATIA**；安装脚本会安装 Windows COM 依赖。
- 支持启动本地 stdio 服务的 MCP 客户端。

原生接口以已验证的 LucidShape 2.0 为基础，部分扩展绑定引擎哈希；其他版本需要重新验证。软件、许可和工程输入由使用者准备。

## 快速开始

### 1. 安装

下载或克隆源码，在项目根目录打开 PowerShell，运行：

```powershell
py -3.12 scripts/bootstrap.py --lucidshape-exe "C:\Program Files\LucidShape\BIN64GPU\lush.exe"
```

将路径替换为本机的 `lush.exe`。脚本创建独立环境、安装依赖，在 `local-data/config/` 生成客户端配置，并验证 MCP 启动。首次安装需要网络或可用的 pip 依赖缓存。

需要 OCCT 几何检查时追加 `--with-geometry`；仅检查 MCP 服务可运行 `py -3.12 scripts/bootstrap.py --minimal`。更多选项见 [安装指南](docs/installation.md)。

### 2. 连接客户端

从 `local-data/config/` 选择对应文件，将其中的服务器条目合并到客户端现有配置，然后重新连接 MCP。安装脚本不会修改客户端全局设置。

| 客户端 | 生成文件 |
| --- | --- |
| Claude Desktop / Claude Code | `claude-desktop.local.json` / `claude-code.local.json` |
| Cursor | `cursor.local.json` |
| VS Code / Copilot CLI | `vscode.local.json` / `copilot-cli.local.json` |
| Codex | `codex.local.toml` |
| OpenCode | `opencode.local.json` |
| 其他客户端 | 见 [全部 14 种配置](docs/harness-compatibility.md#客户端与配置文件) |
| DeepSeek Harness（DSH） | 不生成文件，需装配置型组合包；见 [DSH 接入](docs/dsh.md) |

配置使用本机解释器和绝对路径。换电脑或移动项目目录后，重新生成配置。

### 3. 发起第一个请求

连接后，先让助手检查服务能力：

```text
调用 LucidShape MCP 的 get_capabilities，列出当前启用的工具和配置的引擎路径。
```

准备好模型后，可以发送：

```text
检查 D:\OpticalProjects\demo\scene.lue，列出光源、材料、传感器和几何对象。
```

也可以在终端检查安装：

```powershell
local-data\venv\Scripts\python.exe -m lucidshape_mcp check
```

`check` 验证协议连接和工具发现；原生引擎、许可及 GPU 能力需要通过 MCP `health_check` 和小场景另行验证。

## 客户端配置

优先使用安装脚本生成的配置。服务入口为 `python -m lucidshape_mcp` 或 `lucidshape-mcp`，不带参数时启动 stdio 服务。

<details>
<summary>手动配置：通用 mcpServers JSON</summary>

将下面的示例路径替换为实际路径。VS Code、OpenCode 等使用不同结构，请选用生成器对应的输出。

```json
{
  "mcpServers": {
    "lucidshape": {
      "command": "C:/tools/lucidshape-mcp/local-data/venv/Scripts/python.exe",
      "args": ["-m", "lucidshape_mcp"],
      "env": {
        "LUCIDSHAPE_EXE": "C:/Program Files/LucidShape/BIN64GPU/lush.exe",
        "LUCIDSHAPE_WORK_DIR": "C:/lucidshape-jobs",
        "LUCIDSHAPE_TOOLSETS": "core,studio,catia,geometry,analysis,simulation",
        "LUCIDSHAPE_TOOLS": "",
        "LUCIDSHAPE_READ_ONLY": "false",
        "LUCIDSHAPE_ALLOWED_ROOTS": "[]",
        "LUCIDSHAPE_ALLOW_SOURCE_WRITES": "false"
      }
    }
  }
}
```

可复制的文件版本：[mcp.example.json](examples/mcp.example.json)。个人配置放在 `local-data/`，不提交 Git。

</details>

<details>
<summary>环境变量与工具权限</summary>

| 环境变量 | 用途 / 默认值 |
| --- | --- |
| `LUCIDSHAPE_EXE` | `lush.exe` 绝对路径；未配置时不能提交原生批处理 |
| `LUCIDSHAPE_WORK_DIR` | 任务目录；未配置时为用户目录下 `.lucidshape-mcp/work` |
| `LUCIDSHAPE_KERNEL_PATH` | 可选外置 OCP 运行库路径；也可安装 `geometry` 依赖 |
| `LUCIDSHAPE_TOOLSETS` | 默认 `core,studio,catia,geometry,analysis,simulation`；`all` 包含实验组和旧别名 |
| `LUCIDSHAPE_TOOLS` | 精选工具名，逗号分隔；始终保留 core |
| `LUCIDSHAPE_READ_ONLY` | 仅开放保护源文件和 CAD 状态的查询，默认 `false` |
| `LUCIDSHAPE_ALLOWED_ROOTS` | 允许输入目录的 JSON 数组；默认 `[]`，按本机用户权限访问 |
| `LUCIDSHAPE_ALLOW_SOURCE_WRITES` | 允许 `catia_show_all` 保存源文档，默认 `false` |

安装、`configure` 和 `serve` 也支持 `--toolsets`、`--tools`、`--read-only`、`--allow-root` 和 `--allow-source-writes`。具体范围见 [任务与权限](docs/operations.md)。

</details>

## 工具与使用流程

当前版本 **0.8.0** 默认开放 **37 个工具**。启用 `experimental` 后为 **46 个**；`all` 另含 8 个旧别名，共 **54 个**。工具的参数和返回结构以客户端 `tools/list` 为准。

| 任务 | 常用工具 |
| --- | --- |
| 检查与导出 CAD | `catia_inspect_part`、`catia_export_step`、`inspect_cad_geometry` |
| 连接现有 LucidStudio | `studio_get_live_state`、`studio_inspect_current`、`studio_import_step` |
| 计算已保存模型 | `inspect_model`、`simulate_model`、`run_parameter_sweep` |
| 分析与导出配光 | `analyze_lid`、`compare_photometry`、`export_ies` |
| 管理任务 | `get_job`、`list_jobs`、`cancel_job`、`read_job_log` |

例如，为已配置好光源、材料和传感器的模型生成配光文件：

```text
使用 D:\OpticalProjects\demo\scene.lue，以 10000 条光线运行仿真。
等任务成功后分析光度结果，再导出 IES，并给出结果文件路径。
```

批处理工具返回 `job_id`，每一步都应轮询 `get_job`，确认 `status == succeeded` 后再使用结果。完整说明见 [工具目录](docs/tools.md)。

自建场景使用 [optical-scene/1 配置](docs/generalization.md)，支持 1–64 个共用 RAY 的光源、自定义坐标基、命名工况及逐材料损耗。从 [场景示例](examples/optical-scene.example.json) 和 [几何清单](examples/region-manifest.example.json) 开始，按真实输入和检查回执填写。

## 支持范围

- **客户端**：已验证 14 种配置的格式和 MCP SDK 启动链路；各客户端应用及版本仍需实测。当前提供本地 Windows stdio，远程 HTTP、WSL/容器和云端执行需额外适配。
- **光学计算**：通用场景组合仍需原生光学和收敛验证。`trace_optical_gpu_control` 明确省略额外逐穿透吸收；固定逐穿透损耗的 GPU 候选仍禁用。出射 RAY 回放只重算传感器。
- **工程结论**：IES 导出不包含 GB/ECE 合格认证或 C-NCAP 评分。原生模型可携带脚本，应使用可信输入；路径策略不是原生代码沙箱。

验证层次及后端限制见 [服务架构](docs/architecture.md)、[通用场景说明](docs/generalization.md) 和 [发布准备说明](docs/release-readiness.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [安装指南](docs/installation.md) | 安装、升级、环境诊断和常见问题 |
| [客户端兼容](docs/harness-compatibility.md) | 各客户端配置格式及接入位置 |
| [DSH 接入](docs/dsh.md) | DeepSeek Harness 的组合包接入、字段对应与低完整性级别陷阱 |
| [工具目录](docs/tools.md) | 工具用途、参数和执行边界 |
| [场景配置与迁移](docs/generalization.md) | 材料、光源、分区、工况和旧接口迁移 |
| [任务与权限](docs/operations.md) | 任务状态、取消、文件访问和源文档保存 |
| [开发指南](docs/development.md) | 测试、构建、安装验证和源码导出 |

## 开发与贡献

在项目根目录创建开发环境并运行检查：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m unittest discover -s tests -q
.venv\Scripts\python.exe scripts/check_repository.py
```

几何测试按需安装 `.[geometry]`。单元测试使用模拟引擎和合成夹具；原生验证需要本机软件及许可，步骤见 [开发指南](docs/development.md)。

源码、文档、通用示例及测试纳入 Git；工程数据、归档、个人配置、运行库和产物统一保存在被忽略的 `local-data/`。提交改动时附上复现步骤和验证方式，真实客户模型或厂商资产留在本机。

## 许可证

本仓库尚未声明开源许可证，发布前由维护者确定代码授权范围。LucidShape、CATIA 及其许可、安装包、原厂手册和客户工程数据不随源码分发。
