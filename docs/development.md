# 开发与测试

## 目录结构

| 目录 / 文件 | 职责 |
| --- | --- |
| `start.py` | 本地服务启动、单实例锁、端口选择与浏览器打开 |
| `backend/` | FastAPI 接口与 SQLite 数据层 |
| `frontend/` | 浏览器界面与交互 |
| `providers/` | 路线服务抽象、高德与模拟提供器 |
| `services/` | 导入导出、任务执行、研究计划与质量处理 |
| `utils/` | 坐标转换、路径与版本 |
| `resources/` | 内置地点与测试输入 |
| `tests/` | 后端、启动器和前端研究流程测试 |
| `scripts/` | 打包、打包验证和离线基准 |

## 安装与启动

按根目录 README 创建虚拟环境并安装 `requirements.txt`。默认启动只绑定本机回环地址：

```bash
python start.py
python start.py --no-browser --data-dir ./local-data --port 8000
```

`--port` 为优先端口（0–65535，0 表示自动选择），被占用时会尝试其他端口。`--data-dir` 优先于 `TRAFFIC_DATA_DIR`，两者均未设置时使用系统用户数据目录。`--no-browser` 不会降低会话鉴权，也不代表自动显示带令牌地址；通常仅用于测试或外部管理场景。

不要将本机服务改为公网监听后直接提供多人访问；当前设计是个人本地应用。

## 测试

```bash
python -m pip install pytest
python -m pytest -q
node --check frontend/app.js
node tests/test_frontend_research.cjs
```

前端测试不需要 npm 安装；需要 Node.js（CI 使用22）。修改导入、任务状态、导出字段或研究计划后，需运行对应测试及全量回归。

自动化测试中的模拟或 HTTP 替身不证明真实高德额度、性能或可用性。不要在普通 CI 中注入真实地图 Key 来执行批量请求。

## 打包

```bash
python -m pip install pyinstaller
python scripts/build.py
python scripts/smoke_package.py
```

在目标系统原生构建：Windows 生成单文件 `.exe`；macOS 生成应用包。输出位于 `dist/`。不要把构建目录、数据库或私人研究输入提交到源码仓库。

## 离线基准

```bash
python scripts/benchmark.py
python scripts/benchmark_throughput.py
```

这些脚本用于测量本机准备、持久化、调度或模拟吞吐，不代表真实地图服务吞吐。结果受硬件、运行环境和配置影响；请记录环境及命令，不把离线组合数当作真实 API 调用数。

## CI

GitHub Actions 分别执行前端语法与研究流程测试，以及 macOS / Windows Python测试、原生打包与打包验证。运行记录中的 JUnit 报告与打包产物可用于检查本次构建；失败任务不应当作可分发版本。
