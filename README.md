<div align="center">

# 交通可达性分析系统

**把重复的路线查询，组织成可追溯的研究数据采集流程。**

面向 GIS、旅游管理与交通地理研究的本地工具<br>
批量地点导入 · 多日定时采集 · 去返程查询 · 质量核查 · CSV / Excel 导出

[![测试与构建](https://github.com/gshota666/traffic-accessibility-system/actions/workflows/test-build.yml/badge.svg)](https://github.com/gshota666/traffic-accessibility-system/actions/workflows/test-build.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![运行方式](https://img.shields.io/badge/运行方式-本地应用-2f5785)

[快速开始](#快速开始) · [功能](#核心功能) · [研究案例](docs/product-case.md) · [使用指南](docs/user-guide.md) · [开发与测试](docs/development.md)

</div>

---

## 为什么做这个工具

研究乡村旅游目的地的自驾可达性时，需要反复查询多个客源地到多个目的地的道路距离与预计行车时间。手动逐条查询难以保持时间记录、路线方向和数据格式一致，也不方便检查漏采。

本项目将这项工作组织为 **导入地点 → 配置计划 → 执行查询 → 检查质量 → 导出数据** 的本地流程，帮助研究者把精力放在数据核对和后续分析上。

> 本项目是研究数据采集工具，不是导航软件。高德返回的是实际查询时的路线预计耗时，不是车辆实测时间，也不是历史或未来预测路况。模拟模式的数据不能用于研究结论。

## 核心功能

| 环节 | 能做什么 | 对研究的价值 |
| --- | --- | --- |
| 地点管理 | CSV / Excel 导入、字段映射、坐标检查、稳定编号 | 在执行查询前发现输入问题 |
| 坐标处理 | WGS84、GCJ-02、BD-09 记录与转换 | 区分原始坐标和路线查询坐标 |
| 任务配置 | 批量起终点、多日期、多时段、去程与返程 | 将重复采集需求组织为任务 |
| 执行管理 | 状态查看、失败重试、暂停与恢复 | 跟踪任务进度和异常 |
| 质量核查 | 记录实际请求时间，区分成功、失败、待采与错过时间 | 避免把缺测误作不可达 |
| 研究导出 | 路线明细、汇总、请求记录、字段说明 | 支持后续清洗、核验与可达性计算 |

**地图服务：**真实路线查询使用高德 Web 服务 Key；初次体验可使用默认的模拟模式，不必配置 Key。

## 实际研究应用

这个工具最初源于自己的研究需求：在多个起终点、日期和时段之间反复采集路线数据，手动查询和整理十分繁琐。将这些步骤串成任务后，便可以统一安排采集，再核对结果并导出分析。

目前已在实际研究中累计完成 **23,986 次 API 请求，首次请求成功率 100%**。这一统计对应已完成的采集任务，原始调用日志未公开；其他任务的表现仍取决于网络、账户及接口状态。

从需求拆解、原型框架到功能规划，重点始终是让工具适应研究流程，程序实现则交由 AI Agent 完成。具体设计思路见 [产品案例](docs/product-case.md)。

## 快速开始

### Windows：使用打包版

1. 打开 [Actions 构建记录](https://github.com/gshota666/traffic-accessibility-system/actions/workflows/test-build.yml)，选择一次成功的 **Native platform test and build** 运行。
2. 在 **Artifacts** 下载 `traffic-accessibility-windows-latest` 并解压。下载 Actions 产物通常需要登录 GitHub，产物也可能过期；无法下载时可从源码运行。
3. 打开 `dist` 文件夹，双击 `交通可达性分析系统.exe`。程序启动本地服务并打开浏览器。
4. 先使用模拟模式体验；需要真实数据时，在系统设置中配置高德 Web 服务 Key。

这是便携应用，不是安装向导；使用结束后，通过页面中的“退出应用”关闭本地服务。

### macOS / Linux：从源码运行

需要 Python 3.11 或更新版本。

```bash
git clone https://github.com/gshota666/traffic-accessibility-system.git
cd traffic-accessibility-system
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python start.py
```

### Windows：从源码运行

在 PowerShell 中执行，无需修改脚本执行策略：

```powershell
git clone https://github.com/gshota666/traffic-accessibility-system.git
cd traffic-accessibility-system
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe start.py
```

### 第一次使用

1. **导入小样本：**使用 `resources/test_points.csv` 体验字段映射与校验。该文件包含用于测试的异常记录，不是正式研究数据。
2. **选择模拟模式：**建立少量起终点组合，检查任务创建、状态和导出流程。
3. **准备正式地点：**确认编号、经纬度、坐标系及实际导航入口。
4. **配置高德并试采：**确认服务权限、额度和少量真实查询结果后，再安排批量任务。
5. **检查并导出：**核对采集窗口、缺测、失败与有效路线，保留原始记录。

完整操作与常见问题见 [使用指南](docs/user-guide.md)。

## 运行与数据边界

- **本机运行：**服务仅监听 `127.0.0.1`，默认尝试端口 8000，冲突时选择其他端口；不是公网多人协作服务。
- **定时启动：**指定的是发起查询的时间；一批路线不是在同一秒完成。分析时应核对每条记录的实际请求时间。
- **保持运行：**定时采集期间电脑需要开机、联网、不休眠，应用也需要保持运行。
- **保留缺失：**失败、无路线和错过窗口不等于不可达；缺失路况不推算为畅通或拥堵。
- **数据与凭据：**数据库、导出和日志存放于本地用户数据目录；Key 默认使用系统凭据库。明文存储需用户明确选择，不要上传本地数据目录。
- **调用额度：**本机限流不代表高德账户授权；实际结果受 Key 权限、额度、接口与网络影响。

## 技术组成

| 层次 | 实现 |
| --- | --- |
| 界面 | HTML、CSS、JavaScript，本地浏览器交互 |
| 本地 API | Python、FastAPI、Uvicorn |
| 数据持久化 | SQLite |
| 路线服务 | httpx 对接高德驾车路线服务；独立模拟提供器 |
| 数据读写 | CSV、openpyxl |
| 桌面打包 | PyInstaller，在目标系统原生构建 |

## 开发与参与

- [开发指南](docs/development.md)：目录结构、启动参数、测试与构建。
- [产品案例](docs/product-case.md)：需求、功能决策、个人与 AI 的分工、验证边界。
- [贡献指南](CONTRIBUTING.md)：问题反馈与变更验证。
- [安全反馈](SECURITY.md)：凭据及敏感数据问题的反馈方式。

欢迎提交可复现的问题或功能建议。反馈时请去除 API Key、会话令牌和非公开研究资料。
