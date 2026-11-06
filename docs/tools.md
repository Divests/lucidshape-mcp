# MCP 工具目录

当前服务默认开放 37 个工具，启用 `experimental` 后共 46 个规范工具；另启用 `legacy` 的 8 个旧别名后共 54 个。客户端 `tools/list` 返回的 schema 为调用依据；分组、只读、文件权限和升级行为见 [任务和权限](operations.md)。

## 返回方式

- 批处理返回 `job_id`，通过 `get_job` 轮询。成功须经过引擎退出码、完成标记、产物及结构化结果校验。
- `studio_*` 会话操作返回直接回执。连接不可用或未验证不能解释为执行成功。
- `read_job_log` 读取诊断日志；`get_capabilities` 只报告配置，不证明应用许可、物理模型或当前 GPU 能力有效。

| 工具 | 功能 |
| --- | --- |
| `get_capabilities` | 查看引擎路径和可用模板；不启动软件 |
| `catia_get_state` | 只读连接已运行的 CATIA，返回文档路径、当前文档、Saved 与 ReadOnly 状态 |
| `catia_inspect_part` | 只读检查已打开的指定 CATPart，返回零件属性、参数、特征名称、根轴系和几何参数中的轴系、Part/Body 材料查询；不打开副本或保存修改 |
| `catia_export_step` | 备份后复用已打开的目标文档导出 STEP；默认拒绝未保存内容，明确指定 `include_unsaved:true` 可导出当前内存几何。目标未打开时才使用私有副本，仅关闭确认由该任务新建且未修改的副本文档 |
| `catia_show_all` | 备份 CATPart 后解除全部可搜索对象的隐藏，复查隐藏数为 0；原有未保存修改或文档路径别名需明确授权 `save_as_source` 才另存覆盖目标 |
| `catia_convert_step_to_stl` | 在新建私有文档中通过 CATIA 原生接口将 STEP 转为 STL；验证文档归属、保留已有文档，仅关闭本任务已保存的文档；默认网格精度未经光学验证 |
| `catia_export_body_stl` | 显式传 body_name，将已打开 CATPart 的指定实体复制到新建私有零件并导出 STL；不重开源文件，不保存源文档，保留用户文档和选择；CATIA 自有剪贴板临时文档会被 Copy 替换 |
| `import_cad_geometry` | STEP 多曲面几何转换为 `.lue`，校验保存前后曲面计数和包围盒；失败结果不验收 |
| `studio_open_file` | 通过原生 `Application.openFile/getModel` 在 Studio 打开 STEP 或原生文件，直接返回加载回执 |
| `studio_get_live_state` | 通过 DDE 连接现有实例，校验可执行文件路径并返回进程 ID；不启动软件 |
| `studio_inspect_current` | 读取现有实例当前场景的名称、对象数、曲面数、尺寸和已打开模型数，验证请求对应的原生回执 |
| `studio_get_api_catalog` | 读取运行时命令/类型目录，支持筛选、分页及指定类型的方法帮助；保留引擎哈希与原始输出，不把 `...` 当作完整签名 |
| `studio_inspect_objects` | 分页读取当前模型对象引用，以及已支持具体类型的材质、出口材质和曲面关系；区分未赋值与未查询 |
| `studio_get_simulation_settings` | 按安装包参数索引读取 Monte Carlo、光映射和多进程数值设置；光线数为实际条数，不启动仿真 |
| `studio_import_step` | 在现有实例中新建文档加载 STEP 快照，校验模型身份、几何和之后的两次 API 响应；不启动新实例 |
| `catia_create_plane_mirror` | 在新 CATPart 中创建矩形镜面并导出 STEP；宽高 1–1000 mm、绕 Y 轴转角 −15° 至 15° |
| `prepare_cad_mirror_scene` | 将一个 STEP 根曲面作为镜面，添加定向点光源和远场传感器，输出 `.lue` |
| `health_check` | 默认保存 Point3；`check_gpu:true` 运行 10,000 光线固定 GPU 镜面测试，检查模式和 CPU 回退；返回 job_id |
| `submit_demo_simulation` | 提交点光源与远场光强传感器示例，参数为 rays、flux_lm、timeout_seconds |
| `inspect_model` | 检查已保存的 `.lue/.lug`，返回对象名称、类型，以及光源材料、传感器、几何、作用材料和光度数据分组 |
| `inspect_cad_geometry` | 独立批处理读取 STEP/STL 的曲面计数、包围盒及任意非空对象名称；不改变 Studio，不证明区域赋材正确 |
| `inspect_rayfile` | 使用原生 RayFile 接口识别格式、光谱标志并校验全部光线；日志包含光通量和光线起点范围，不修改模型或原文件 |
| `preflight_optical_scene` | 按通用场景配置绑定区域和材料、建立各光源坐标基及探测器，检查轴向路径；诊断不计额外穿透吸收 |
| `validate_traversal_absorption` | 固定玻璃平板的 20000 条随机光路及全反射验证，检查每次完整进入/离开只扣一次损耗；样例不是工程仿真结果 |
| `trace_optical_paths` | CPU 独立抽样追迹，按每种材料的完整进出次数累计配置损耗，输出各命名工况的加权 RAY；拒绝不配对介质及直接异种介质转换 |
| `trace_optical_gpu` | 固定逐穿透损耗的 GPU 候选仍禁用，启动前拒绝；改名不会消除入射 Fresnel 反射偏差 |
| `trace_optical_gpu_control` | 按显式光源、工况、坐标及探测器做 GPU 对照，省略配置中的额外穿透损耗，保留 Fresnel/TIR；等权共享非光谱 RAY |
| `merge_optical_batches` | 核验连续完整源覆盖、相同配置及逐单元相加，按命名工况合并；旧 low/additional 报告经兼容规则处理 |
| `replay_optical_boundary_paths` | 在明确指定的折射区域及可选反射面中重跑失败光路检查点；起点介质需独立验证，坐标缩放仅作诊断 |
| `repair_optical_mesh` | 依据哈希一致的诊断报告修复显式 target_region 的局部网格副本，限制位移和法线变化，返回验收/拒绝；仍需原生和光路复核 |
| `bin_rayfile` | 将加权出射 RAY 逐条归入 Type-B 配光；sensor_config 配置坐标、角域和网格，默认沿用历史 X 向前网格；不重新追迹几何 |
| `replay_rayfile_batch` | 连续记录范围的 Type-A 出射 RAY 回放，支持 sensor_config、光通量倍率和俯仰角，独立逐格校验；不重新追迹几何或材料 |
| `inspect_optical_region_topology` | 通过 OCCT 检查任意命名 STEP 区域的拓扑、边界和内外点；可显式提供 reference/coating/transmitting 角色及局部 target_region |
| `prepare_optical_regions` | 按 region_roles 缝合并定向参考实体对应的镀层/透射两类曲面分区，另存 STEP/STL/法线网格；支持显式附加闭合实体，保留原件 |
| `cancel_geometry_job` | 兼容旧入口，仅接受当前任务目录的几何任务记录，通过所有者处理取消；不终止 CATIA 或 Studio |
| `simulate_model` | 对已保存的模型运行正向追迹，每个传感器导出一份独立 `.lid` |
| `analyze_lid` | 分析二维非光谱坎德拉 LID 或 IES（IES 使用原生 IESData），返回峰值及角度、角度范围内光通量、可选区域光通量 |
| `compare_photometry` | 比较同网格、同角度约定的二维非光谱 LID/IES，返回有正负号的候选减参考差异、RMSE、MAE、角域通量变化和 CSV |
| `inspect_optical_parameters` | 从保存的模型副本读取支持的光源参数、部分介质字段、材质关系和传感器元数据；不读取当前 Studio |
| `set_emitter_parameters` | 为唯一名称的 PointLight 克隆 LambertEmitter，修改通量或角度并另存回读，保留原文件和其他共享该材质的光源 |
| `run_parameter_sweep` | 对同一保存模型运行 1–16 组独立 CPU 参数试验，固定随机种子，每组输出 LID 和汇总 CSV |
| `export_ies` | 用原生接口将二维非光谱坎德拉 LID 导出为 IES，回读并逐格核对光强、角度和网格，再独立检查文件结构；不执行仿真或法规判定 |
| `get_job` | 查询状态、输入文件清单、结构化 result 和已验证的产物路径 |
| `list_jobs` | 按状态筛选并分页列出任务、阶段、耗时和所属实例 |
| `cancel_job` | 向已验证的所属实例请求取消任务，只停止它拥有的工作进程；需轮询确认终态 |
| `read_job_log` | 获取任务日志末尾 |

## 调用注意

现有 CATIA 会话中的未保存修改不会默认写回源文件。需要导出当前内存状态或保存全部改动时，分别使用明确的 `include_unsaved`、`save_as_source` 参数并确认操作范围。

`studio_import_step` 会在已有实例中加载新文档；超时后原生加载可能仍在执行，不应自动重复提交。`studio_open_file` 是可能启动新实例的旧入口。

参数修改及扫描仅覆盖已核验的对象类型和参数向量，不支持任意光源、任意材质或任意引擎版本。光度比较要求相同坐标、网格与量纲，不隐式重采样。

通用场景工具依赖调用方的配置、分区清单和原生检查记录；字段、限额与旧名称映射见 [通用化说明](generalization.md)。仓库不附真实灯具资产。固定透镜穿透损耗的 GPU 候选被禁用，对照工具明确使用不同的损耗定义。所有工程结论仍需要几何、材料、光源和收敛验证。
