# AI 行程规划 Web 应用

一个在浏览器中使用的 AI 行程规划应用，适合个人与朋友共同筹划旅行。从选择城市、收集景点和美食，到生成每日行程、调整安排、规划地图路线和导出旅行计划，在同一个网页中完成。

支持电脑和手机浏览器。前端使用原生 HTML、CSS 和 JavaScript，后端使用 Python 标准库与 SQLite，无需前端构建或安装 Python 第三方依赖。

## 主要功能

- **AI 规划行程**：接入 DeepSeek，按城市、日期和偏好生成行程、景点与美食；先预览、编辑和选择，再导入项目。支持重新规划、补全地点信息，以及单独生成出行提示。
- **按天安排行程**：切换日期，新增空白日，调整地点顺序、时段和停留时间，管理备选地点；支持夜间安排和时段快速跳转。
- **地图与路线**：接入高德，确认地点位置，规划步行、骑行、公交地铁和驾车路线，保存路线并在行程地图中查看。
- **城市与地点清单**：搜索或添加旅行城市，维护景点、美食、分类和标签；支持景区等级筛选、行程关联和城市轨道交通图。
- **多人协作**：一个部署可创建多个独立项目，项目可设置密码或无密码进入。同行者选择用户 ID 作为编辑署名，共享行程并自动同步，遇到编辑冲突时保留草稿。
- **导出与离线查看**：将当前城市或整个项目导出为一张 PNG，包含高德底图、已保存路线和每日行程。每段仅展示实际确认的起点、终点和交通方式；也支持 Word、Excel。
- **后台管理**：管理项目和用户，修改项目密码，配置 DeepSeek 与高德密钥。应用内提供使用说明与分步操作引导。

## 使用流程

1. 打开网页，选择已有项目，或新建项目并选择是否设置密码。
2. 进入项目后选择或创建用户 ID，再选择旅行城市。
3. 使用 AI 生成完整行程，或先收集景点、美食，再手动按天安排。
4. 核对 AI 草稿并确认导入，调整每日地点、顺序和停留时间。
5. 确认地图位置与交通方式，规划并保存路线。
6. 按需生成出行提示，下载整体行程图，或导出 Word、Excel，分享给同行者。

AI 生成内容需要自行核对；天气、营业时间、票价和预约要求以目的地实际信息为准。修改行程后应重新核算路线并导出文件。

## 本地运行

需要 Python 3.9 或更高版本。Node.js 仅用于前端测试，运行应用不需要 Node.js。

```sh
git clone https://github.com/hgqchn/travel-planner.git
cd travel-planner
python3 -c 'import sys; print(sys.executable); print(sys.version)'
```

设置管理员密码并启动：

```sh
export TRIP_ADMIN_PASSWORD='替换为你的管理员密码'
python3 -B server.py
```

打开 [本地应用](http://127.0.0.1:8000)，后台入口为 `/admin.html`。默认数据目录是 `data/`；首次启动会初始化数据库，后续启动保留已有数据。

`TRIP_PROJECT_CODE` 可选，仅用于初始化默认项目密码。不设置时，新初始化的默认项目无密码；已有项目的密码以数据库为准。未配置管理员密码时，管理员登录不可用。

应用启动后，可在后台的“API 密钥设置”配置 AI 和地图服务。也可通过环境变量提供配置：

| 环境变量 | 用途 |
| --- | --- |
| `TRIP_ADMIN_PASSWORD` | 管理员登录密码 |
| `TRIP_PROJECT_CODE` | 默认项目的初始密码，可选 |
| `TRIP_HOST` / `TRIP_PORT` | 监听地址与端口 |
| `TRIP_DATA_DIR` | 持久化数据目录，默认 `data/` |
| `DEEPSEEK_API_KEY` | 启用 DeepSeek AI |
| `DEEPSEEK_MODEL` | 覆盖应用默认模型名称 |
| `AMAP_JS_KEY` | 高德 JavaScript 地图 Key |
| `AMAP_SECURITY_JS_CODE` | 高德 JavaScript 安全密钥 |
| `AMAP_WEB_SERVICE_KEY` | 高德地点搜索与路线服务 Key |
| `TRIP_PUBLIC_ORIGIN` / `TRIP_COOKIE_SECURE` | 公网访问来源与 HTTPS Cookie 配置，见部署文档 |

后台保存的密钥位于数据目录的 `api-settings.json`，权限为 `0600`，优先于对应环境变量。后台仅显示配置状态，不回显密钥。没有配置 AI 或高德时，仍可手动维护旅行计划。

已有本地预览环境的启动方式见 [本地预览说明](LOCAL_PREVIEW.md)；高德开通及配置步骤见 [高德地图配置](AMAP_SETUP.md)。

## 服务器部署

支持 Docker 和直接运行 Python 两种方式：

- [Docker 部署与更新](DOCKER_DEPLOY.md)：现有阿里云部署、镜像构建、配置、备份及更新步骤。
- [Linux 部署](DEPLOY.md)：使用 systemd 和反向代理部署的参考流程。

Docker 镜像包含应用代码和静态资源，数据目录需要持久化挂载。更新前先备份；不要用本机数据库或配置覆盖服务器数据。公网部署应配置 HTTPS。

## 数据与备份

每个旅行项目使用独立 SQLite 数据库；同一部署共享景点、美食资料缓存，项目的行程和用户信息保持隔离。用户 ID 是协作署名，不是独立密码账号。项目列表公开展示项目名称及是否需要密码，旅行内容在进入对应项目后访问。

备份全部项目数据库及共享地点缓存：

```sh
python3 -B backup_all.py --data-dir data --output backups/travel-snapshot
```

输出目录必须尚不存在。此命令备份 SQLite 数据；迁移时还需安全保留 `api-settings.json`、环境配置，以及需要保留的 `metro_maps/` 下载资源。数据、密钥和本地备份不应提交到 GitHub。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `public/` | Web 页面、样式、交互和内置地图资源 |
| `server.py` | HTTP 服务、接口和应用启动入口 |
| `ai_service.py`、`daily_ai.py`、`ai_planning_prompts.py` | AI 任务、每日规划与提示词 |
| `daily_planner.py`、`daily_plan_store.py` | 每日计划与持久化 |
| `amap_service.py`、`route_image.py` | 高德服务与离线路线图片 |
| `project_store.py`、`place_cache.py` | 多项目管理与地点缓存 |
| `itinerary_export.py` | Word、Excel 导出 |
| `resources/`、根目录 JSON 文件 | 城市、景区及初始化资料 |
| `deploy/`、`Dockerfile` | 部署配置和运维脚本 |
| `dev/` | 本地启动与资料维护工具 |
| `tests/` | 后端与前端回归测试 |
| `docs/archive/` | 历史设计、实现计划和调研记录，不代表当前功能承诺 |
| `data/` | 本地数据和配置，不纳入版本控制 |

## 测试

```sh
python3 -B -m unittest discover -s tests
node --test tests/*.cjs
```

后端接口测试会监听本机临时端口，需要允许本地网络监听。使用 NVM 管理 Node.js 时，先加载 NVM 并选择已有版本。

## 资料与维护说明

- [景区等级与数据来源](SCENIC_RATINGS.md)
- [城市轨道交通图、来源与使用许可](METRO_MAPS.md)
- [高德地图配置与路线使用](AMAP_SETUP.md)

本 README 是项目定位、当前功能和使用入口的主说明；专题文档提供部署与数据维护细节，历史归档用于追溯设计过程。
