# 安装与首次运行

## 前提

原生功能需要 Windows、有效的 LucidShape 安装及许可。推荐使用本项目已验证的 Python 3.12 64 位；CI 同时覆盖 Python 3.11 的基础测试。CATIA 操作还要求使用者安装并打开 CATIA。仓库不携带这些商业软件、客户模型或光线文件。

## 从 ZIP 或 Git 安装

解压或克隆源码，在项目根目录运行：

```powershell
py -3.12 scripts/bootstrap.py --lucidshape-exe "C:\Program Files\LucidShape\BIN64GPU\lush.exe"
```

使用实际引擎路径；安装目录不同不需要改代码。如果没有 `py` 启动器，可改用已确认版本的 `python`。脚本会：

1. 在 `local-data/venv` 创建不继承系统依赖的虚拟环境。
2. 安装本项目、分析依赖，以及 Windows 下的 CATIA COM 依赖。
3. 在 `local-data/config` 生成 14 种配置，包括通用 JSON、Codex TOML 和各客户端适配格式。
4. 从项目外的临时目录启动已安装的 MCP，检查工具发现和能力查询。
5. 保存 `doctor.json` 和本次依赖版本清单 `installed-requirements.txt`。

这个过程不启动 CATIA/LucidShape、不运行仿真，也不改客户端全局设置。安装 Python 依赖需要网络或自行配置的包缓存。

需要 OCCT 几何检查时追加 `--with-geometry`。只安装 MCP 服务时追加 `--minimal`，可以不提供引擎路径；缺少的原生能力会明确显示为不可用。可用 `--venv`、`--work-dir`、`--config-dir` 更改目录，中文和空格路径也可使用。

## 连接客户端

按 [Harness 兼容说明](harness-compatibility.md) 选择对应配置。通用 JSON 为 `mcp.local.json`，Codex 为 `codex.local.toml`；VS Code、OpenCode 等必须使用其专用输出。将服务器条目合并到客户端已有配置中，避免重复定义同名条目。生成器不会覆盖已有的客户端配置。

DeepSeek Harness（DSH）不读取这些文件，需要把 `mcp.local.json` 里的启动字段搬进一个配置型组合包，并且虚拟环境要装在 DSH 工作区之外；步骤见 [DSH 接入](dsh.md)。

Codex 的 stdio 配置使用 `command`、`args` 和 `env`；生成的 TOML 格式依据 [OpenAI 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。客户端重连后，默认应能发现 40 个工具（启用实验组后为 49 个，`all` 包含旧别名共 57 个）。

生成文件使用本机解释器和绝对路径，只留在 `local-data`，不上传 Git。换电脑只传源码，重新安装；目录改名后重新运行安装/配置命令，不把旧虚拟环境和配置当作可移植安装包。

## 检查与重新生成配置

```powershell
local-data\venv\Scripts\python.exe -m lucidshape_mcp check
local-data\venv\Scripts\python.exe -m lucidshape_mcp doctor --lucidshape-exe "C:\Program Files\LucidShape\BIN64GPU\lush.exe" --work-dir local-data/work --require engine --require catia
```

`doctor` 只在指定任务目录创建并删除一个写入探针，同时在独立 Python 子进程中检查依赖导入。`status=passed` 表示请求的环境条件满足；`license_status=not_checked` 和 `native_execution_verified=false` 仍然保留。真正的原生检查通过 MCP `health_check` 执行，之后再使用合成场景做首次试算。

需要单独重新生成配置时：

```powershell
local-data\venv\Scripts\python.exe -m lucidshape_mcp configure --harness all --lucidshape-exe "C:\Program Files\LucidShape\BIN64GPU\lush.exe" --work-dir local-data/work --output-dir local-data/config --force
```

没有 `--force` 时不会覆盖已生成的配置。更新源码后，重新运行安装脚本并加 `--force-config`，完成后重新连接 MCP。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 找不到 `lush.exe` | 提供实际可执行文件路径，不是软件安装目录或 `LucidShape.exe` |
| 能发现工具，仿真不能提交 | 检查配置中的引擎路径，再运行原生 `health_check` 验证安装与许可 |
| 缺少 NumPy、pywin32 或 OCP | 在源码目录通过对应解释器安装 `.[analysis]`、`.[catia]`、`.[geometry]`；也可重新运行安装脚本 |
| 依赖已安装但导入失败 | 查看 `doctor.json` 的完整导入错误、Python 位数和依赖运行库；不要只凭包目录存在判断可用 |
| 下载依赖超时 | 安装脚本默认使用 60 秒网络超时和 5 次重试；也可通过 pip 支持的代理、`PIP_DEFAULT_TIMEOUT` 或 `PIP_FIND_LINKS` 配置网络或本地依赖缓存 |
| 光学扩展提示引擎未验证 | 该接口绑定已核验的引擎哈希，需按目标版本重新做接口及数值验证；安装脚本不会跳过限制 |
| 软件/目录换位置后启动失败 | 在新位置重新安装并生成配置，更新客户端条目 |
| `doctor` 通过但 GPU 失败 | 环境诊断没有执行 GPU 追迹；需要独立的许可、GPU 后端和数值验证 |

MCP SDK 使用 `>=1.30.0,<2`，保留 v1 API。官方明确说明 v2 存在不兼容变更，未迁移项目应保留 `<2` 上界，见 [MCP SDK 安装说明](https://py.sdk.modelcontextprotocol.io/get-started/installation/)。
