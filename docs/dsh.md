# 在 DeepSeek Harness 中使用

DeepSeek Harness（DSH）不读取 `mcpServers` JSON，也没有独立的客户端配置文件：它的 MCP 客户端是内置插件 `@deepseek-ai/dsh-mcp-client`，接入方式是给当前 profile 装一个**配置型组合包**，在组合树里插入一条 stdio 服务器记录。本页说明这种接入、验证方法，以及两个会在 DSH 里必现的陷阱。

系统、Python、LucidShape 与许可的前提见[安装指南](installation.md)。

## 1. 安装服务

建议在**普通 PowerShell 窗口**里安装，不要直接在 DSH 会话里装：

```powershell
py -3.12 -m venv C:\lucidshape-mcp\venv
$py = "C:\lucidshape-mcp\venv\Scripts\python.exe"
& $py -m pip install --no-input --no-cache-dir setuptools wheel
& $py -m pip install --no-input --no-cache-dir --no-build-isolation -e "E:\lucidshape-mcp"
& $py -m pip install --no-input --no-cache-dir numpy pywin32
& $py -m lucidshape_mcp check
```

最后一条应输出 `"status": "passed"` 和 40 个工具。

在 DSH 会话里执行 `scripts/bootstrap.py` 或 pip 时，DSH 的文件沙箱会影响安装：pip 写 HTTP 缓存时可能长时间空转（CPU 在涨、没有网络连接、没有输出），临时目录也可能不可写并报 `Permission denied ...\Temp\...\pip-unpack-*`。两种情况都用 `--no-cache-dir` 规避，必要时把 `$env:TEMP`/`$env:TMP` 指到可写目录；仍然不行就换到普通 PowerShell 窗口。

## 2. 解释器必须放在 DSH 工作区之外

**这是最容易踩、也最难自己看出来的坑。**

DSH 的工作区目录（默认 `D:\DSH_WorkSpace`）带一条可继承的 Low 强制完整性标签：

```
Mandatory Label\Low Mandatory Level:(OI)(CI)(NW)
```

工作区里的可执行文件继承该标签，Windows 于是让进程以**低完整性级别**运行。MCP 服务是低完整性，它启动的 `lush.exe` 也是低完整性，而 LucidShape 的保护壳拒绝在这种环境下运行，弹出：

```
lush.exe — E0013 -- Application not allowed to run in debugger.
```

该对话框是模态的，没有人点「确定」，作业就一直挂着：`health_check` 最终 `timed out`，`engine.log` 为 0 字节。

因此**虚拟环境要装在 DSH 工作区之外**（例如 `C:\lucidshape-mcp\venv`）。源码留在工作区里没有影响——源码只被读取，不参与执行。组合包里 `command` 指向的就是这个工作区外的解释器。

自检：

```powershell
# S-1-16-4096 为低完整性（会触发 E0013）；S-1-16-8192 为中（正常）
& $py -c "import subprocess;print([l.split()[-1] for l in subprocess.run(['whoami','/groups'],capture_output=True,text=True).stdout.splitlines() if 'S-1-16' in l])"
```

不改动工作区标签是刻意的：给 venv 目录单独去掉低标签会削弱 DSH 的沙箱，且可能被重新应用。

## 3. 组合包

新建一个目录（放在 DSH 工作区之外更省事），包含这些文件。

`package.json`

```json
{
  "name": "@local/lucidshape-mcp",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "exports": { "./locale/*.json": "./locale/*.json", "./icon": "./icon.svg" },
  "files": ["cordis.patch.yml", "locale/*.json", "icon.svg"],
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
}
```

`cordis.patch.yml`

```yaml
- insert:
    - id: mcp-lucidshape
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: lucidshape
        transport: stdio
        command: 'C:\lucidshape-mcp\venv\Scripts\python.exe'
        args: ['-m', 'lucidshape_mcp']
        env:
          LUCIDSHAPE_EXE: 'C:\Program Files\LucidShape\BIN64GPU\lush.exe'
          LUCIDSHAPE_WORK_DIR: 'C:\lucidshape-mcp\work'
          LUCIDSHAPE_TOOLSETS: 'core,studio,catia,analysis,simulation'
          LUCIDSHAPE_READ_ONLY: 'false'
        toolCallTimeoutMs: 180000
        failOnStartupError: false
```

`env` 的每个值都要写成字符串（YAML 里 `true`/`123` 这类不加引号会被解析成布尔或数字）。

再补 `locale/en.json`（`meta.title`、`meta.description`）和 `icon.svg`，否则插件页显示为无标题。然后用 `plugin_manager` 安装：

```
action: install_bundle
target: <组合包目录的绝对路径>
```

`install_bundle` 会写 profile 的 `package.json` 和组合包列表，不需要手工编辑。

### 与 `mcp.local.json` 的对应

`configure --harness generic` 生成的 `local-data/config/mcp.local.json` 可以直接当素材，字段一一对应：

| `mcpServers.lucidshape` | DSH 侧 |
| --- | --- |
| `command` | `command` |
| `args` | `args` |
| `env` | `env`（值统一写成字符串） |
| — | `serverName: lucidshape`、`transport: stdio` |

## 4. 验证

工具以 `mcp__lucidshape__<工具名>` 出现在会话里。

1. `get_capabilities`：确认 `executable_exists`、`catia.runtime_available`、`tool_count`、`optional_geometry_kernel.available`。
2. `health_check`：跑一次固定引擎冒烟测试。首次启动较慢，`timeout_seconds` 给到 300 以上。
3. 批处理工具返回 `job_id`，必须轮询 `get_job` 到 `status == succeeded` 再取结果。

## 5. 工具组

默认工具组含 `geometry`，其依赖 `cadquery-ocp-novtk` 需要单独安装；未安装时那几个工具仍会注册，调用时才报错。不需要就在 `LUCIDSHAPE_TOOLSETS` 里去掉 `geometry`（此时工具数为 36 而不是 40）。

## 6. 运行约束

- `studio_*` 通过 Windows DDE 连接**已运行**的 LucidStudio，不会启动新实例；`catia_*` 通过 COM 连接已运行的 CATIA。
- 原生作业按用户级文件锁串行，同一时间只跑一个。
- DSH 启动 MCP 子进程时会删除名字含 `KEY`、`PASSWORD`、`SECRET`、`TOKEN` 的环境变量以及所有 `DSH_*` 变量。`SE_LICENSE_SERVER` 不在其中；若许可变量名发生变化，需要在组合包的 `env` 里显式重写（配置里的 `env` 在清洗之后合并）。
- 低完整性环境下只能使用不启动 `lush.exe` 的工具（`catia_*`、`studio_*`、`get_capabilities`、`get_job`、`list_jobs`、`read_job_log`）；批处理类工具会因 E0013 失败。
