# MCP 工具目录

当前服务默认开放 53 个工具，启用 `experimental` 后共 62 个规范工具；另启用 `legacy` 的 8 个旧别名后共 70 个。客户端 `tools/list` 返回的 schema 为调用依据；分组、只读、文件权限和升级行为见 [任务和权限](operations.md)。

## 返回方式

- 批处理返回 `job_id`，通过 `get_job` 轮询。成功须经过引擎退出码、完成标记、产物及结构化结果校验。
- `studio_*` 会话操作返回直接回执。连接不可用或未验证不能解释为执行成功。
- `read_job_log` 读取诊断日志；`get_capabilities` 只报告配置，不证明应用许可、物理模型或当前 GPU 能力有效。

| 工具 | 功能 |
| --- | --- |
| `studio_list_ui_actions`、`studio_get_ui_action` | 按按钮提示、菜单名称、命令符号或路径定位声明。详情包含安装文件、行号、调用入口和参数预设 |
| `studio_run_ui_action` | 用清单中的 `action_id` 和目标工具所需的参数执行已接入的操作；保留目标工具的作业编号、权限和错误回执 |
| `studio_export_ui_catalog` | 在工作目录生成完整 UTF-8 JSON，含菜单、工具栏、对话框控件、来源与当前策略下的工具映射 |
| `studio_list_dialog_controls`、`studio_list_live_menu` | 前者搜索安装脚本中的控件定义。后者读取当前 Studio 窗口公开的原生菜单及启用、勾选状态 |
| `studio_get_help_resource` | 从帮助菜单条目的 ID 找到本机对应 PDF，并核对文件签名 |
| `create_geometry_primitive` | 在新模型中创建给定尺寸的基本曲面或形状，保存 .lue 后重新加载并检查几何 |
| `photometry_apply_ui_action` | 在配光副本上执行原生均化、缩放、镜像、对称等操作，导出 IES 并逐格回读 |
| `measure_photometry_region` | 在已保存的 Type A 坎德拉配光上执行点值、区域极值、全局最小或最大、区域均值、角域光通量和分区光通量测量 |
| `get_capabilities` | 查看引擎路径和可用模板；不启动软件 |
| `list_lighting_standards` | 列出本机已安装的灯光评价体系，按规则 ID 和 ADB 配置查询录入模板 |
| `evaluate_vehicle_lighting` | 提交整车灯光项目，轮询 `get_job` 取得各体系独立分项、缺项及 JSON/XLSX 报告 |
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
| `measure_photometry_table` | 对已照准的 Type A、二维非光谱坎德拉 IES/LID 调用 LucidShape 原生 LidMeasureTable。选择 `gb4599_2024_lb`（基础近光，表 1 A）或 `gb4599_2024_hb`（中性远光，表 5），用 `get_job` 读取逐项光强、限值、判定及原生文本。按文件已有坐标和光通量做工程校核，线 10 及以下保留有限角域标识。 |
| `analyze_lid` | 分析二维非光谱坎德拉 LID 或 IES（IES 使用原生 IESData），返回峰值及角度、角度范围内光通量、可选区域光通量 |
| `compare_photometry` | 比较同网格、同角度约定的二维非光谱 LID/IES，返回有正负号的候选减参考差异、RMSE、MAE、角域通量变化和 CSV |
| `inspect_optical_parameters` | 从保存的模型副本读取支持的光源参数、部分介质字段、材质关系和传感器元数据；不读取当前 Studio |
| `set_emitter_parameters` | 为唯一名称的 PointLight 克隆 LambertEmitter，修改通量或角度并另存回读，保留原文件和其他共享该材质的光源 |
| `run_parameter_sweep` | 对同一保存模型运行 1–16 组独立 CPU 参数试验，固定随机种子，每组输出 LID 和汇总 CSV |
| `export_ies` | 用原生接口将二维非光谱坎德拉 LID 导出为 IES，回读并逐格核对光强、角度和网格，再独立检查文件结构；不执行仿真或法规判定 |
| `generate_road_views` | 从已瞄准的坎德拉配光生成原生道路照度与驾驶员视角亮度；显式灯位、眼点、网格和路面反射率，原生 X前/Y左/Z上、单位米；不重追迹 CAD、不改当前 Studio |
| `studio_export_photometry_view` | 将 IES/LID 副本作为原生 UVView 导出 PNG，保留坐标、色标、网格和等值线；不经过屏幕截图或剪贴板，不隐式转换光度量 |
| `studio_generate_static_road_views` | 由两盏灯的已瞄准配光生成原生 Static Road 场景、径向照度 LID/CSV、驾驶员及高位视角 PNG；使用 LightData 显示，不表示 cd/m² 亮度 |
| `get_job` | 查询状态、输入文件清单、结构化 result 和已验证的产物路径 |
| `list_jobs` | 按状态筛选并分页列出任务、阶段、耗时和所属实例 |
| `cancel_job` | 向已验证的所属实例请求取消任务，只停止它拥有的工作进程；需轮询确认终态 |
| `read_job_log` | 获取任务日志末尾 |

## 调用注意

### 按 LucidShape 按钮查找工具

搜索 `studio_list_ui_actions(query="Basic Smoothing", executable_only=true)`，读取条目的 `execution.tool` 和 `preset_arguments`。例如，在 `photometry_apply_ui_action` 中传入 IES 绝对路径、`command="CM_UV_FILTER"` 和 `passes=1`，即可对副本均化一次。在 `measure_photometry_region` 中传入其输出、`mode="point"`、`coordinates_deg=[-4,2]` 和 `unit="cd"` 可读取同一位置的光强。两次调用均通过 `get_job` 获取结果。

默认 `profile="studio"` 读取当前安装的主菜单及引用定义。`profile="all"` 同时读取其他产品、历史菜单和条件声明。菜单与工具栏可以引用同一个操作。`catalog_only` 表示目前只有定义，`disabled_by_policy` 表示目标工具在当前策略下关闭。`available` 表示有对应的 MCP 工具，具体支持的文件、参数和作用范围见 `execution.scope`。许可、当前模型、选区和对话框状态还会影响现场可用性。

现有 CATIA 会话中的未保存修改不会默认写回源文件。需要导出当前内存状态或保存全部改动时，分别使用明确的 `include_unsaved`、`save_as_source` 参数并确认操作范围。

`studio_import_step` 会在已有实例中加载新文档；超时后原生加载可能仍在执行，不应自动重复提交。`studio_open_file` 是可能启动新实例的旧入口。

参数修改及扫描仅覆盖已核验的对象类型和参数向量，不支持任意光源、任意材质或任意引擎版本。光度比较要求相同坐标、网格与量纲，不隐式重采样。

通用场景工具依赖调用方的配置、分区清单和原生检查记录；字段、限额与旧名称映射见 [通用化说明](generalization.md)。仓库不附真实灯具资产。固定透镜穿透损耗的 GPU 候选被禁用，对照工具明确使用不同的损耗定义。所有工程结论仍需要几何、材料、光源和收敛验证。

## 原生路照与驾驶员视角

`generate_road_views` 属于 `analysis` 组，在私有批任务中创建结果，`read_only` 模式不开放。当前实现绑定已验证的 LS2.0 BIN64GPU 引擎配置。输入为二维非光谱坎德拉 IES/LID；同一份配光复制到一个或两个明确的灯位，不自动镜像。

坐标使用 **X 向前、Y 向左、Z 向上，长度为米**。`road_bounds_m` 为 `[前方最小值, 前方最大值, 左向最小值, 左向最大值]`；`view_bounds_deg` 为 `[水平最小角, 水平最大角, 垂直最小角, 垂直最大角]`，`view_angles_deg` 为原生 `[旋转, 俯仰, 滚转]`。当前验证配置不接受非零旋转或滚转。灯具方向固定朝前，因此输入配光应已经完成所需瞄准；工具不再添加下倾或光通量倍率。

路面采用明确 `road_reflectance` 的理想朗伯模型。该工作流调用 LucidShape 的道路/亮度原生接口，返回原生 LID、图像及校验记录，实际文件以 `get_job.artifacts` 为准。它是配光到道路的投影，不重新追迹整灯 CAD，不包含车身遮挡，也不给出法规合格结论。原生输出不应与未标定曝光的真实人眼照片等同。


## 原生报告图片

`studio_export_photometry_view` 和 `studio_generate_static_road_views` 属于 `analysis` 组，返回直接回执。两者创建派生文件及原生视图，所以不在 `read_only` 模式开放。工具仅接受已验证的 Studio 二进制版本，固定脚本、输入快照和结果校验均写入任务目录。本版使用任务独占的 Studio 进程和固定启动脚本。启动前先读取进程元数据；若已有 Studio，返回 `STUDIO_ALREADY_RUNNING` 且不启动新实例。此时只需保存并关闭已有窗口一次，无需手动重开；没有实例时由 MCP 启动。工具不会替你关闭已有实例，也不通过已有窗口的 DDE 连接执行。导出完成、超时、取消或产物校验失败后，只回收仍持有原始 Popen 句柄的任务进程，保留日志与产物；回收失败会明确返回 `process_left_open: true`。结果须为 `status: succeeded` 才能作为完成的原生导出使用；不要自动重复提交。预检查不能阻止检查后手工启动另一实例，运行期间应避免另行启动 Studio。故障阶段及操作建议见[运行说明](operations.md#原生报告导出)。

### 配光图

`studio_export_photometry_view` 使用 `UVView.snapshotBitmap` 导出带原生坐标及色标的 PNG。`width_px`、`height_px` 指定导出尺寸，`view_bounds` 指定显示范围，`value_range` 为对数色标范围，`contours` 指定等值线。IES 的坐标单位为角度，平面 LID 使用数据自身的坐标单位。

`quantity: candela` 要求坎德拉输入，`quantity: native` 保留原生量纲。`quantity: illuminance_25m` 也只接受坎德拉输入，切换原生视图为 **I/625 的25 m等效照度（lx）**，不修改配光数组或输入文件；它不表示含离轴入射/距离修正的实际平面屏照度，也不支持任意距离。`value_range` 与 `contours` 按当前显示单位填写，先切换显示量、读回属性，再应用范围与等值线；导出回执检查所请求模式和参数。

`display_mode` 可为 `spectrum`（默认）、`grayscale`、`contours`、`color_contours`、`bands`；`scale_mode` 可为 `logarithmic`（默认）、`linear`、`munsell`；`show_contours` 默认 true，控制等值线叠加；`contours` / `color_contours` 模式要求此值为 true，拒绝矛盾设置。`gamma_correction` 默认 null，保留原生显示伽马开关；显式 true/false 会设置并回读校验。它不是曝光倍率，也不改变输入光强；需要线性灰度对照时应显式 false。坐标、网格、色标仍保留。每个组合的图像含义和效果需要按实际导出检查，代码路径存在不等于全部组合已经完成原生验证。

输入单位标志、角度类型、峰值和积分在创建视图及显示设置之前捕获（`source_metrics_capture_stage`）；部分原生显示切换会改变内存数据的单位标志，不能用切换后的标志重新判断输入量纲。回执中的 `native_peak_value_u_v` 始终是捕获的原生输入数据峰值及坐标；25m模式另给 `derived_display_peak_value_u_v`，明确是按 I/625 推导的值，不能宣称独立读取了视图中的峰值文本。`display_transform` 记录显示换算，原始光通量不缩放。报告应依据实际模式标注 cd、lx 等单位。

### Static Road 图

`studio_generate_static_road_views` 要求两个显式灯位 `lamp_positions_m`，同一份二维非光谱 Type A/B 配光用于两灯。输入已包含的下倾和光通量倍率不会再次叠加。长度使用米，X 向前、Y 向左、Z 向上，道路在 Z=0。

`road_bounds_m` 为 `[Xmin, Xmax, Ymin, Ymax]`，`road_cell_size_m` 为道路网格间距。`driver_position_m` 和 `top_position_m` 为相机位置，`driver_target_m` 和 `top_target_m` 使用原生前向目标距离约定。场景包含车道标线及道路末端背景墙。

当前模式为 `illumination_type: radial`，不乘水平路面入射余弦。它与 `generate_road_views` 的水平路面照度及朗伯亮度模式有不同含义。Static Road 的驾驶员和高位图片使用原生 LightData/Munsell 灰度显示，不能标注为 cd/m² 或经过视觉亮度标定的照片。输出的 `road.lid` 可继续交给配光图导出工具，生成带坐标与色标的原生道路图。

这两种工作流都从已有配光计算结果，不重新追迹整灯几何，也不自动得出 GB、ECE 或 C-NCAP 结论。
