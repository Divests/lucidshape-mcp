# 开发与发布准备

## 本地测试

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m unittest discover -s tests -q
.venv\Scripts\python.exe scripts/smoke_mcp.py
```

单元测试不连接 CATIA 或 LucidStudio。几何内核测试需要 `.[geometry]`，缺少时相应测试跳过；其他测试仍须通过。可选外置几何依赖通过 `LUCIDSHAPE_KERNEL_PATH` 指向含 `OCP` 的目录。源码目录优先查找 `local-data/work/cad-kernel-runtime`，并兼容旧 `work/cad-kernel-runtime`；这些运行库不进入 Git 或分发包。

## 原生集成验证

这些命令是显式选择的原生验证，CI 不运行：

```powershell
# 需要运行中的 CATIA；会创建独立测试文档并经 MCP 进行镜面用例计算。
python scripts/validate_full_mcp.py --exe C:/path/to/lush.exe

# 在私有目录用原生 CLI 建立合成夹具，然后通过 MCP 验证光学扩展。
python scripts/validate_optical_extensions.py --executable C:/path/to/lush.exe
```

输出在本地 `local-data/work/` 下，不提交任务回执中可能包含的绝对路径、模型或日志。原生验证需使用有效安装及许可。

## 文件提交范围

`.gitignore` 从根目录白名单开始，只开放源码、测试、文档、通用示例和选定维护脚本。本地数据统一放入 `local-data/`，例如 `local-data/catia`、`local-data/rayfile`、`local-data/results`、`local-data/scripts`。安装脚本生成的个人配置放在 `local-data/config/`；历史配置可保留在 `local-data/examples/mcp.local.json`。公开配置示例为 `examples/mcp.example.json`。历史工程需要旧绝对路径时，可在本机保留目录链接；它们不进入 Git。

```powershell
git status --short
python scripts/check_repository.py
```

检查器同时审查已跟踪和未忽略文件，即使 `git add -f` 强行加入客户数据，也会按源文件范围拒绝。它还检查文件大小、UTF-8、符号链接、常见密钥及个人绝对路径模式；这属于基础检查，不是完整敏感信息检测。

新增维护脚本时同时更新 `.gitignore`、`scripts/check_repository.py`、`MANIFEST.in`。合成夹具可加入测试；不要把真实光线文件或厂商示例当作公开测试数据。

## 干净下载验证

```powershell
python scripts/verify_fresh_install.py --with-geometry
```

该检查把 Git 可见源码复制到一个含中文和空格的临时目录，再执行 `bootstrap.py`。新虚拟环境不继承系统依赖，安装后的 MCP 从源码目录外启动；同时验证生成的 JSON/TOML 启动参数一致，逐份检查 14 种客户端配置的 MCP SDK 连接，并运行完整单元测试。需要联网获取依赖。它不复制本机 `local-data`、模型、兼容链接或已下载的几何运行库。

安装阶段刻意使用 `PYTHONIOENCODING=cp1252:strict`，检查重定向输出遇到中文路径时仍能完成。安装器保留调用者选择的编码，只转义无法表示的字符；子进程继续使用 UTF-8。权限测试还覆盖 Windows 短路径与长路径指向同一允许目录的情况，并确认目录外的依赖仍被拒绝。

`scripts/smoke_mcp.py` 继续覆盖旧的 `python -m lucidshape_mcp.server` 入口；新安装流程覆盖 `python -m lucidshape_mcp` 入口。

`scripts/verify_harness_configs.py` 可以单独执行，但它调用 MCP SDK，不启动客户端应用。新增配置适配器时须补充其 JSON/TOML 字段契约测试和实际 stdio 启动检查，不能将这两项表述为第三方客户端实机认证。

## 分发包检查

```powershell
python -m build --outdir local-data/dist/packages
python scripts/verify_distribution.py
```

`verify_distribution.py` 默认检查 `local-data/dist/packages`，要求目标目录恰好有一个 wheel 和一个 sdist。它检查全部包文件和固定模板，拒绝额外资产，再从项目外的临时目录使用解包后的 wheel 启动实际 stdio MCP。该检查使用当前 Python 中已安装的运行依赖，不证明所有第三方依赖可以离线安装。

## 生成可上传源码包

```powershell
python scripts/check_repository.py --export local-data/dist/lucidshape-mcp-source.zip
```

导出内容与 Git 可见源文件范围一致，另保存 SHA256 清单；目标已存在时拒绝覆盖。ZIP 不包含 `.git`、CAD、RAY、结果、安装包或个人配置。发布前自行确定许可证及目标仓库；这些脚本不会创建提交或推送。

## 自动检查

GitHub Actions 配置在 Windows Python 3.11 和 3.12 上执行单元测试、无引擎 stdio 检查及源码包构建，并另在 Python 3.12 上执行含 geometry 的干净安装验证。只有推送到配置了 Actions 的仓库后才会运行；本地通过不能表述为远端 CI 已通过。

## 通用场景回归

`tests/test_optical_scene.py` 使用合成的任意区域名、多光源坐标、多材料及自定义工况，覆盖模板、CPU 损耗、GPU 数量回执、批次覆盖和旧别名参数。OCCT 用例实际构造几何、导出 STEP 并运行独立工作进程。`test_ray_replay.py` 检查旋转坐标基与非等角宽网格的独立通量积分。这些不执行 LucidShape 原生光学追迹。旧工程输入约束集中在 `test_legacy_profile.py`，不得用放宽旧规则代替新配置实现。
