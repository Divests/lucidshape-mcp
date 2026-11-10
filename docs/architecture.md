# 服务结构与使用边界

## 处理链

```text
MCP 客户端
  -> server.py 参数 schema 与工具路由
  -> engine.py 参数校验、串行队列、任务目录、超时
  -> CATIA COM 工作进程 / lush.exe 固定模板 / 可选 OCCT 工作进程
  -> results.py 及专项校验器
  -> job.json / result.json / 原生产物 / engine.log
```

现有 LucidStudio 会话另由 `dde_session.py` 和 `dde_worker.py` 维护 Windows DDE 通信，`live_studio.py` 与 `studio_queries.py` 校验进程、请求标识和模型身份。MCP 服务与工作进程之间不传递 COM 对象。

## 模块划分

| 模块 | 责任 |
| --- | --- |
| `server.py` | 47 个规范工具入口、服务生命周期、环境配置；默认注册 38 个，另有 8 个旧别名 |
| `tool_api.py`、`contracts.py`、`policy.py` | 结构化响应、参数约束、行为标记、工具组和权限检查 |
| `job_control.py`、`coordination.py` | 所有权锁、跨实例查询/取消、心跳、分页与跨进程会话锁 |
| `config.py` | 用户可写的任务目录及可选几何库位置 |
| `engine.py`、`results.py` | 调度、超时、记录、原生结果校验 |
| `road_simulation.py` | 原生路照与驾驶员视角的参数约束、安装脚本快照和结果校验 |
| `inputs.py` | 输入及依赖快照、路径与体积边界 |
| `catia*` | 会话、零件检查、可验证文档归属及转换 |
| `studio*`、`dde*`、`live_studio.py` | 原生 Studio 启动或已存在会话的受限操作 |
| `extensions.py` | 光学参数、光源副本修改、CPU 扫描、同网格比较 |
| `photometry.py`、`ray_replay.py` | IES 结构与大型出射光线的独立数值校验 |
| `kernel*`、`boundary*`、`mesh*` | 可选几何诊断、边界检查和派生网格修复 |
| `optical_scene.py`、`scene_utils.py` | 通用场景配置、公共校验、输入快照和坐标处理 |
| `gpu_trace.py`、`gpu_merge.py`、`path_sampling.py` | 依配置追迹、工况输出、损耗及批次覆盖校验 |
| `legacy_profile.py`、`compatibility.py` | 旧项目约束、旧工具与参数映射；两个旧模块名仅作导入转发 |
| `templates/*.do` | 随 Python 包发布的固定 LucidShell 模板 |

## 文件与应用影响

任务数据写入 `LUCIDSHAPE_WORK_DIR`；未配置时写入用户目录 `.lucidshape-mcp/work`。默认不依赖当前终端目录，不向安装目录写任务数据。调用方可继续显式指定旧的项目工作目录。

输入副本、依赖文件和源哈希属于可追溯记录。修改类工具生成派生结果；CATIA 的显示全部及授权保存功能会影响用户文档，应以工具说明中的范围为准。固定模板并不使输入模型成为安全沙箱，只处理可信模型及依赖。

服务采用本地 stdio，没有配置 HTTP 服务、远端认证或多人共享队列。它以启动服务的用户权限访问本机文件和应用。

同一用户的多个 MCP 实例使用操作系统文件锁协调原生访问；查询外部实例的任务不改写记录。文件锁释放不等于 CATIA 内部操作已终止，超时后仍需验证应用状态。见 [任务管理和权限边界](operations.md)。

## 验证层次

1. 单元测试和模拟引擎验证参数、协议、隔离、状态与数学处理。
2. 原生小场景验证本机引擎、API、坐标与特定材料行为。
3. 实际工程还需要源安装、几何质量、材料映射、采样和网格收敛验证。

前一层通过不等于后一层通过。GPU 菜单存在、进程存在或任务退出码为零，都不足以独立证明 GPU 追迹和工程结果正确。

## 当前保留限制

- 部分光学参数解码与会话查询仅接受已核验引擎哈希或安装包索引。
- 输出 RAY 记录数不能还原原始发射数、loops 或递归设置。
- 固定每次完整透镜穿透损失与体吸收、每次界面损失不等价。
- `trace_optical_gpu` 的有偏差候选仍拒绝调用；对照入口不计额外穿透损耗，必须保留结果标签。
- 工具不自动判定车辆照明法规或 C-NCAP 得分。

通用场景字段、兼容入口和当前后端能力范围见 [通用化说明](generalization.md)。
