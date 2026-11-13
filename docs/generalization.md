# 通用场景配置与 0.8 迁移

0.8 将场景输入、材料绑定、光源坐标、工作模式和探测器改为显式配置。新项目使用 `optical-scene/1`，无需把零件改名为 S9、AL 或 IN，也不再限定 12 颗 LED、7+5 点亮组合或统一 0.2% 穿透损耗。

## 名称与兼容

| 旧工具名 | 新工具名 |
| --- | --- |
| `preflight_s9_optics` | `preflight_optical_scene` |
| `trace_s9_paths` | `trace_optical_paths` |
| `trace_s9_gpu` | `trace_optical_gpu` |
| `trace_s9_gpu_control` | `trace_optical_gpu_control` |
| `merge_s9_gpu_batches` | `merge_optical_batches` |
| `replay_s9_boundary_paths` | `replay_optical_boundary_paths` |
| `repair_s9_main_lens_mesh` | `repair_optical_mesh` |
| `prepare_inner_lens_regions` | `prepare_optical_regions` |

新入口将 `requirements_path` 改为 `scene_path`，`rays_per_led` 改为 `rays_per_source`，`pc_normal_policy` 改为 `normal_policy`。旧工具别名仍接受旧参数名，启用 `legacy` 组或 `--toolsets all` 后可见；新实验功能在 `experimental` 组。默认 40 个工具，启用实验组为 49 个，加上 8 个旧别名共 57 个。

核心实现为 `optical_scene.py`、`scene_utils.py`、`gpu_trace.py`，原生模板采用 `*_optical_*` 名称。`s9_scene.py` 和 `gpu_s9.py` 仅为旧 Python 导入保留转发；旧工程校验和默认值集中在 `legacy_profile.py`。旧任务类型可继续读取，结果中的 `rays_per_led`、`PC_*` 等历史字段按兼容约定保留，新场景增加通用材料、光源和工况字段。

历史工程和仿真产物保留 S9 文件名以追溯来源。不要批量重命名 `local-data/` 内的模型、RAY、结果或旧报告；其中可能存在绝对路径和哈希引用。源码上传与这些私有文件无关。

## 新项目需要的两个文件

复制 [场景示例](../examples/optical-scene.example.json) 和 [几何清单示例](../examples/region-manifest.example.json) 到自己的工程目录，填写真实数据。示例不含可运行的 CAD/RAY，哈希为占位值，`native_import_verified` 为 false；必须依据实际读取回执补齐，不能直接改成 true 作为验证替代。

场景文件包含以下字段：

| 字段 | 配置内容 |
| --- | --- |
| `schema_version` / `name` | 固定 `optical-scene/1`，以及项目逻辑名称 |
| `length_unit` | 当前支持 `mm`，CAD 和 RAY 起点必须使用同一长度单位 |
| `materials` | 材料库，见下表 |
| `region_materials` | 几何区域逻辑 ID 到材料 ID 的一一绑定 |
| `rayfile` | 一个已检查的非光谱 Lucid 二进制 RAY，绝对路径、SHA256、记录数、单源总光通量 |
| `sources` | 1–64 个光源；每个有唯一 ID、`position_mm` 和正交右手 `frame.x/y/z` |
| `modes` | 1–16 个工况，值为光源 ID 列表，如 `marker`、`boost`；可重叠，每个光源至少属于一个工况 |
| `sensor` | Type-B 远场角域 `bounds_deg=[u0,u1,v0,v1]`、`grid_uv=[nu,nv]` 和坐标基 |
| `diagnostics` | 可选 `coordinate_scale`，默认 1；其他值仅用于显式采样诊断，不能当成光学修正 |

光源局部位置和方向均通过坐标基转换到模型坐标。`frame.x/y/z` 是局部三个单位轴在模型坐标中的方向，不能只填旋转角或省略朝向。当前所有光源共用同一份 RAY 和单源通量，尚不支持不同型号源文件或逐源调光；光通量字段必须与文件头一致，不会暗中缩放光源。

| `kind` | 必填参数 | 含义 |
| --- | --- | --- |
| `refractive` | `index`、`traversal_absorption_fraction` | 折射率及每次完整空气→介质→空气过程的吸收比例，比例 0–1，不是体吸收系数 |
| `specular` | `reflectance` | 理想镜面反射，反射率 0–1 |
| `Lambertian` | `reflectance` | 理想朗伯反射，反射率 0–1，区分大小写 |
| `absorber` | 无其他参数 | 不透光、完全吸收 |

逻辑 ID 使用字母开头的 ASCII 字母、数字、下划线或连字符，最长 64 字符；实际文件路径和 CATIA `body_name` 可以包含中文。材料最多 64 个，区域最多 128 个。探测器每轴 2–4096 格，总计不超过一百万格；角域各边在 −90° 至 90° 范围内。输入快照合计上限 256 MiB，文件和清单中的哈希必须一致。

几何清单的每个区域包含 `label/path/sha256`。用于建光学场景的区域还需 `include_in_optical_scene=true`、`native_import_verified=true`、`cad_face_count` 和 `native_top_level_object_count`，并且必须恰有一个材料绑定。参考实体设为不参与光学场景，不能同时赋材造成重复边界。STEP、STL/LUG 的曲面计数含义不同；网格还需实际 `mesh` 元数据，不能套用示例中的 STEP 计数。

## 调用流程

1. 用 `catia_inspect_part`、`inspect_cad_geometry`、`inspect_rayfile` 读取源数据。`catia_export_body_stl` 现在必须显式传 `body_name`，不默认选择 S9-LENS。
2. 准备分区清单、材料绑定、光源位置和坐标基，运行 `inspect_optical_region_topology`。通过 `region_labels` 选择任意区域；局部边界检查用 `target_region` 和探测点明确定位。
3. `preflight_optical_scene(scene_path=..., region_manifest_path=...)` 检查原生曲面、材料类型、源坐标和轴向路径。这一步不计额外穿透吸收，不是配光仿真。
4. `trace_optical_paths(..., rays_per_source=2000)` 做 CPU 抽样路径追迹，按所配置材料分别累计完整穿透损耗，为每个工况输出 `<mode>-weighted.ray`。每源 100–10000 条抽样，不能超过源记录数。
5. 用 `bin_rayfile(..., sensor_config=场景文件中的sensor对象)` 生成同坐标、同网格 Type-B 配光，随后 `export_ies`。省略 `sensor_config` 会沿用历史 X 向前默认值；新项目应显式传入。

GPU 对照用 `trace_optical_gpu_control`，每源每批 100–500000 条；只有明确选择该接口才省略配置中的额外穿透吸收，保留 Fresnel/TIR。`sample=false` 按连续记录分批保持绝对权重；`sample=true` 是全功率抽样估计，不能作为全光线覆盖合并。用批次报告的 `jobs`、`status=succeeded_provisional`、`canonical_requirements_unchanged=true` 提交 `merge_optical_batches`，服务仍会核对每个任务、覆盖区间、配置、原生执行回执和文件哈希。

通用 GPU 工况独立计算：若一个光源同时属于两个工况，会分别追迹并计入两次实际输入数；CPU 路径结果则按成员关系复用于对应工况。合并不会把全部光源误固定为 12 颗，也不会把两个不同工况自动当成近光/远光相加。历史 low/additional 格式仅通过旧适配规则组合。

`replay_rayfile_batch` 支持同结构的 `sensor_config`，但输出为 **Type A**，不能与 Type B 当作同一角度约定比较。回放先把光线变换到指定坐标基，再施加 `pitch_deg`；其水平角为负局部 X 方向、竖直向上为局部 Y、前方为局部 Z。它只重算已有出射光线，不重新追迹光学几何。

## 分区和边界工具

`prepare_optical_regions` 通过 `region_roles={"reference":"完整实体ID","coating":"镀层曲面ID","transmitting":"透射曲面ID"}` 指定现有二类曲面分区。`additional_solid_regions` 可加入其他闭合实体，当前要求 `geometry_representation="normal_mesh"`。`normal_policy` 选择透射区域的法线处理。没有显式角色时，仅旧 AL/IN 配置能走兼容推断；不会按名字猜新零件的用途。

`repair_optical_mesh` 必须指定 `target_region` 和与源哈希相符的诊断报告，输出派生副本及验收/拒绝状态。`replay_optical_boundary_paths` 用 `boundary_region` 指定折射区域，可用 `region_labels` 加入对应反射面；它隔离一种折射介质，起点所处介质仍需几何证明。新入口默认从路径第 0 段检查，旧别名保留第 12 段默认值。网格修复或正常退出均不自动代表几何和光学已验收。

## 验证与保留限制

本次重构覆盖配置、模板生成、光线坐标变换、损耗数值校验、工况合并、MCP 参数兼容，以及真实 OCCT 合成几何的读取/分区测试。测试不连接用户 CATIA/LucidShape，尚未新增任意通用场景的原生光学实测；需在实际软件中用小场景验证材料、坐标和能量，再做收敛检查。

CPU 历史路径按折射率区分介质，因此不同材料不能具有无法区分的折射率；可共享同一材料 ID，或改用经过验证的其他后端。直接相接的两种介质不在当前算法范围内，遇到时拒绝验收。完全没有正权重出射光线的工况不能生成有效 RAY。原生模板仍采用固定的追迹深度/模式，不是任意光学算法编排器。

`trace_optical_gpu` 的固定逐穿透损耗候选仍禁用；新名称不会消除其物理偏差。GPU 当前要求等权非光谱源 RAY；光谱、BSDF、多源文件、薄膜、温度模型、通用多材料接触边界及任意多类曲面分区尚未实现。引擎版本、许可、几何精度和法规判定仍有独立要求，不能将接口抽象表述为任意灯具都已验证可算。
