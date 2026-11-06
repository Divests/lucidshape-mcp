# Harness 与 MCP 客户端兼容

本项目提供一个本地 stdio MCP 服务和 14 种配置输出，覆盖下表中的 13 类客户端及通用格式。不是每个客户端都读取相同 JSON；配置生成器负责转换启动字段，仿真引擎保持不变。官方配置资料核对日期：2026-10-27。

## 生成配置

首次安装默认生成全部配置，不需要安装这些客户端：

```powershell
py -3.12 scripts/bootstrap.py --lucidshape-exe "C:\Program Files\LucidShape\BIN64GPU\lush.exe"
```

替换为实际引擎路径。只需要部分客户端时，可重复传入 `--harness`，例如 `--harness cursor --harness opencode`。

已安装的环境可单独生成配置并查看适配器列表：

```powershell
local-data\venv\Scripts\python.exe -m lucidshape_mcp harnesses
local-data\venv\Scripts\python.exe -m lucidshape_mcp configure --harness all --lucidshape-exe "C:\Program Files\LucidShape\BIN64GPU\lush.exe" --work-dir local-data/work --output-dir local-data/config
```

`configure` 不指定 `--harness` 时保留旧行为，仅生成通用 JSON 和 Codex TOML。`--harness all` 必须单独使用，不能与其他选择混合。已有目标文件时拒绝覆盖；确需更新生成文件时，给 `configure` 加 `--force`，或给安装脚本加 `--force-config`。

## 客户端与配置文件

以下文件均生成在 `local-data/config/`。把所选文件中的服务器条目合并到目标配置，保留客户端原有内容；不要直接覆盖整份客户端配置。

| `--harness` | 客户端 / 官方资料 | 生成文件 | 接入位置或方法 |
| --- | --- | --- | --- |
| `generic` | 通用 `mcpServers` JSON | `mcp.local.json` | 支持相同结构的本地 MCP 客户端 |
| `codex` | [Codex，OpenAI Docs](https://learn.chatgpt.com/docs/extend/mcp) | `codex.local.toml` | 合并到 `~/.codex/config.toml` |
| `claude-desktop` | [Claude Desktop](https://modelcontextprotocol.io/docs/develop/connect-local-servers) | `claude-desktop.local.json` | Windows：`%APPDATA%/Claude/claude_desktop_config.json` |
| `claude-code` | [Claude Code](https://code.claude.com/docs/en/mcp) | `claude-code.local.json` | 项目 `.mcp.json` |
| `cursor` | [Cursor](https://prod.cursor.com/help/customization/mcp) | `cursor.local.json` | `~/.cursor/mcp.json` 或项目 `.cursor/mcp.json` |
| `vscode` | [VS Code 本地 MCP 客户端](https://code.visualstudio.com/docs/agents/reference/mcp-configuration) | `vscode.local.json` | `.vscode/mcp.json`；使用顶层 `servers` |
| `copilot-cli` | [GitHub Copilot CLI](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) | `copilot-cli.local.json` | `~/.copilot/mcp-config.json`，设置 `COPILOT_HOME` 时以其为准；也可用便携项目 `.mcp.json` |
| `gemini-cli` | [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/) | `gemini-cli.local.json` | 合并到 `~/.gemini/settings.json` |
| `windsurf` | [Windsurf / Devin Desktop Cascade](https://docs.devin.ai/desktop/cascade/mcp) | `windsurf.local.json` | 通过 Cascade 设置打开原始 MCP 配置；当前 Devin Windows 文档位置为 `%APPDATA%/devin/mcp_config.json` |
| `cline` | [Cline](https://docs.cline.bot/mcp/mcp-overview) | `cline.local.json` | MCP Servers → Configure MCP Servers；CLI 可使用自己的 `cline_mcp_settings.json` |
| `roo-code` | [Roo Code](https://roocodeinc.github.io/Roo-Code/features/mcp/using-mcp-in-roo/) | `roo-code.local.json` | 项目 `.roo/mcp.json` 或 Edit Global MCP |
| `opencode` | [OpenCode](https://opencode.ai/docs/mcp-servers/) | `opencode.local.json` | 合并到 `opencode.json` / `opencode.jsonc`；使用 `mcp`、命令数组和 `environment` |
| `continue` | [Continue](https://docs.continue.dev/customize/deep-dives/mcp) | `continue.local.json` | 放到项目 `.continue/mcpServers/lucidshape.json`；官方支持直接读取这种 JSON |
| `kiro` | [Kiro 本地 IDE / CLI](https://kiro.dev/docs/mcp/configuration/) | `kiro.local.json` | `~/.kiro/settings/mcp.json` 或项目 `.kiro/settings/mcp.json` |

VS Code 当前提供多种 harness 和配置目标：本地 `.vscode/mcp.json` 与 Copilot 便携配置的结构不同，分别选择 `vscode`、`copilot-cli` 输出。Windsurf 的官方文档目前跳转到 Devin Desktop，因此旧版客户端应以设置中打开的实际配置文件为准。生成器不自动查找、迁移或改写这些客户端文件。

所有输出使用已安装 Python 的绝对路径、参数数组和显式任务目录。路径支持中文和空格，不依赖工作目录、激活虚拟环境、`cmd /c` 或复制本机 `PYTHONPATH`。生成的绝对路径属于本机配置，不应上传 Git；换电脑重新运行安装脚本。

## 验证层级与范围

自动检查覆盖 JSON/TOML 解析、各格式字段、Unicode/空格路径、配置选择和防止覆盖。在独立虚拟环境中，还会逐份读取生成文件，使用其中的启动参数建立真实 MCP stdio 连接，执行初始化、发现与当前策略一致的工具、核对全部输出 schema 和 annotations，并调用 `get_capabilities`。

```powershell
local-data\venv\Scripts\python.exe scripts/verify_harness_configs.py
python scripts/verify_fresh_install.py --with-geometry
```

这些连接由 MCP SDK 发起，没有逐个启动上述客户端应用。因此结果应表述为“配置格式与 MCP 启动链路验证通过”，不能表述为“所有 harness 所有版本实机兼容”。客户端自己的信任、工具审批、超时、组织策略仍需在所用客户端验证。生成器不启用全局自动审批；Copilot 的 `tools: ["*"]` 表示工具可见范围，不是自动授权。

## 不属于本轮兼容范围

- 原生 CATIA COM、LucidStudio DDE 和 `lush.exe` 仍需运行于安装了相关软件及许可的 Windows 环境。客户端位于 WSL、容器、SSH 远端或云端时，必须另行解决 Windows 进程启动、用户会话和路径访问，不能直接套用这些配置。
- 当前服务只暴露本地 stdio，没有 HTTP/SSE 服务地址。仅接收远程 URL 的 harness 需要独立设计 Windows 端 HTTP 网关、身份认证、文件传输及会话管理；改一个 JSON 字段不能提供这些能力。
- 当前版本通过同一 Windows 用户下的操作系统文件锁串行协调原生访问；锁不控制用户手动操作、旧版本服务或其他账号/机器。具体行为见 [多客户端任务](operations.md#多客户端任务)。
- 本轮不改变引擎版本限制、光学模型和 GPU 数值验证范围，详见 [发布准备说明](release-readiness.md)。

## 增加其他客户端

在 `lucidshape_mcp/harnesses.py` 的目录中增加客户端 ID、输出文件名、官方文档和接入位置；若字段不同，在 `render_config` 增加转换。随后增加配置契约测试，并更新 `verify_harness_configs.py` 对该格式的读取方式及本页说明。不能仅凭产品支持 MCP 就宣称它支持本地 stdio 或同一种配置结构。
