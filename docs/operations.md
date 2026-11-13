# 任务管理、权限与版本升级

## 工具选择

默认 40 个工具：`core,studio,catia,geometry,analysis,simulation`。`experimental` 包含 9 个通用场景实验工具，包括配置追迹、分区修复及 GPU 对照；显式启用后共 49 个规范工具。`legacy` 包含 8 个旧名称兼容入口，`--toolsets all` 共开放 57 个。指定实体 STL 导出已归入 CATIA 组，必须显式传实体名称。0.8 的名称/参数/场景迁移见 [通用化说明](generalization.md)。

安装时可以指定：

```powershell
py -3.12 scripts/bootstrap.py --toolsets analysis,geometry --read-only --allow-root "D:\OpticalProjects"
```

已有安装也可用 `python -m lucidshape_mcp configure --output-dir local-data/config --harness all --toolsets all --force` 生成新片段。更新后需要重新连接客户端；配置生成器不会修改客户端全局设置。

`--tools inspect_rayfile,analyze_lid` 精选工具时，仍保留 core；`--read-only` 最后进一步过滤。全默认组的只读模式开放 18 个工具。被过滤的工具不会注册，直接按名称调用也不会执行。行为 annotations 仅供客户端理解，实际权限由服务端检查。

`catia_show_all` 可能保存源文档，所有调用均需 `--allow-source-writes` 或 `LUCIDSHAPE_ALLOW_SOURCE_WRITES=true`；其中 `save_as_source` 仍按原有规则控制未保存修改和路径别名。只读模式始终禁用此工具。

## 文件边界

`--allow-root` 可重复指定，环境变量形式为 `LUCIDSHAPE_ALLOWED_ROOTS=["D:/OpticalProjects"]`。目录必须存在，输入路径必须绝对；解析符号链接/目录链接后的路径也要处于允许目录中。原生模型快照和 JSON 清单所引用的已识别文件也经过检查。任务目录始终允许访问，便于后续分析产物。

默认空列表保持可信本地用户文件访问方式。只读模式仍可在任务目录写日志、快照、报告，含义是保护源文件和 CAD 文档状态，并非磁盘零写入。路径策略限制工具接收的文件，不约束原生引擎执行不可信模型脚本，也不隐藏当前 CAD 会话本身返回的信息；不是系统权限沙箱。使用可信模型、私有本地任务目录，不以网络共享目录作为跨主机协调机制。

## 多客户端任务

每个任务记录所有者实例 ID、PID、时间戳和独占文件锁。进程退出后操作系统释放锁；PID 只用于诊断，不用于猜测并终止进程。其他 MCP 实例可读取同一任务目录，读取不会改写任务状态。

`get_job` 报告 `status`、`recorded_status`、`owner_active`、`phase`、`elapsed_seconds`、`heartbeat_at` 和 `poll_interval_ms`。阶段为 `queued`、`waiting_for_session`、`running`、`verifying`、`finished`，属于生命周期进度，未提供没有引擎依据的光线完成百分比。运行时约每 2 秒更新心跳；同步输入准备/结果校验可能推迟更新。

`list_jobs(status=null, offset=0, limit=50)` 返回分页摘要，limit 最大 200；它扫描当前任务目录，不是多主机数据库。`cancel_job(job_id)` 写入与所有者匹配的取消请求，所属实例收到后停止自己的工作进程。应继续轮询至终态；取消回执不是原生 CAD 已停止的证明。输入同步准备期间无法立即处理取消。旧 `cancel_geometry_job` 通过当前配置的任务目录定位后转发同一机制，不再扫描系统进程或依命令行终止进程。

各工作目录的原生批处理和 Studio 操作使用当前用户临时目录中的同一文件锁串行执行；取消时保留会话锁，等待自有工作进程退出后释放。锁不会阻止用户手工操作 CAD，也不会控制旧版本 MCP 或其他账号/机器。关闭服务取消自己拥有的任务，不终止 CATIA/Studio 应用。

所有者不可验证时，查询返回派生 `interrupted` 和 `OWNER_UNAVAILABLE`，保留磁盘记录原样，不自动重试。0.6 的旧任务没有所有权锁：已完成结果仍可读取，遗留活动任务只能标为未验证。升级前应等旧任务结束，再退出旧服务并启动新版；新版不能替旧服务取消任务。DDE 超时或 COM 工作进程被停止后，CAD 内部工作仍可能继续；确认应用状态后再发起新操作。

## 返回协议

所有工具都有 `outputSchema` 和 `structuredContent`，保留原有顶层字段。公共结构版本为 `schema_version=1.0`。错误含 `error_details.code/message/retryable`，常见代码有 `ACCESS_DENIED`、`INVALID_ARGUMENT`、`FILE_NOT_FOUND`、`JOB_TIMEOUT`、`JOB_CANCELLED`、`OWNER_UNAVAILABLE`。同步工具错误设置 MCP `isError=true`；异步任务失败通过任务 `status` 表达。SDK 在执行前发现参数类型或范围错误时仍使用标准 MCP 工具错误。

公共任务、日志、取消、分页和错误字段有类型约束；专项物理 `result` 和部分 Studio 回执仍按可扩展对象返回，并由原有结果校验器验证，尚未为每一种物理对象定义独立 schema。本版使用普通工具提交和轮询，未声明支持 MCP 原生 Tasks 协议、断点续算或通知式光线进度。
