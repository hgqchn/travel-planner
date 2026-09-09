# 旅游规划

面向手机浏览器的多人旅行规划网页。一个部署对应一个共享项目，项目可包含多个城市，上海作为默认展示。同行者先用项目口令进入，再选择或创建无需密码的用户 ID；所有人都可以新增、编辑和删除行程、景点与美食，修改会显示编辑者和时间。

## 特点

- 原生 HTML、CSS、JavaScript，没有前端依赖或构建步骤。
- Python 3.9+ 标准库后端，没有 `pip install`。
- SQLite 单文件持久化，适合一台小服务器和小规模同行群组。
- 项目口令保护整个共享空间；未解锁时不会返回用户、内容或活动数据。
- 进入项目后可在已有用户之间无密码切换，也可创建新的唯一 ID。
- 独立行程规划页按日期和时间整理每日安排。
- 每 5 秒以 ETag 轻量同步；无变化时服务端返回空的 `304` 响应。
- 每条记录带版本号；两个人同时编辑时返回 `409`，不会静默覆盖。
- 冲突时保留本地草稿，展示对方改动，并要求用户明确载入最新版或准备覆盖。
- 用户 ID 由 SQLite 唯一约束保证不重名；设备会话令牌仅存于 `HttpOnly` Cookie，数据库只保存令牌哈希。
- 所有动态文字使用 `textContent` 渲染；外链只接受 `http://` 与 `https://`。
- 项目口令为必填启动配置；公网模板还会限制请求体、并发线程、注册与写入频率、分类条目数量。

## 本地运行

请先确认 Python 3.9+ 的版本和实际路径：

```sh
command -v python3
python3 --version
python3 -c "import sys; print(sys.executable)"
```

设置项目口令后启动：

```sh
cd /path/to/travel-planner
TRIP_PROJECT_CODE='换成你的项目口令' TRIP_ADMIN_PASSWORD='换成你的管理员密码' python3 server.py
```

浏览器访问 <http://127.0.0.1:8000>，管理员入口为 `/admin.html`。数据库首次启动时创建为 `data/trip.db`，导入 `seed_data.json` 和 `shanghai_extra.json`（26 个上海景点、22 种美食）。普通重启不会覆盖现有数据。

当前 Mac 本地配置保存在不纳入 Git 的 `data/local.env`（仅本机用户可读写），可在项目目录重新启动：

```sh
set -a
. ./data/local.env
set +a
/Library/Developer/CommandLineTools/usr/bin/python3 -B server.py
```

该解释器路径仅适用于当前 Mac；Linux 部署请按部署文档确认路径，不要上传本地口令文件。

## 管理员与城市

- 管理员密码通过 `TRIP_ADMIN_PASSWORD` 设置，与项目口令独立；修改该环境变量并重启即可更换管理员密码。
- 管理员可修改项目名称和项目口令，以及添加、改名、删除用户和城市。
- 项目口令可自由设置为 1–200 个字符，不要求字母、数字组合；后台留空表示不修改。公网使用仍建议设置较长且不易猜测的口令。管理员密码不限制长度或字符组合；未设置或为空时禁用管理员登录。
- `TRIP_PROJECT_CODE` 仅用于首次建库或从旧版本升级时初始化；之后以数据库中的加盐哈希为准。后台修改的口令重启后仍然有效，旧项目访问凭证立即失效。
- 用户改名或删除会使其设备身份会话失效，需要重新选 ID；删除用户不会删除旅行内容。ID 只是署名，同行者仍可自建 ID，不适用于严格身份审计。
- 页面顶部切换城市；新建行程、景点和美食归属当前城市。非空城市不可直接删除，至少保留一个城市。
- 景点增加可编辑导航链接，留空时默认打开高德地点搜索。小红书攻略为“上海 + 名称”的搜索链接，不是已核验的具体笔记，仍可手动替换。
- 交通页显示上海轨道交通图和高德、百度地图入口；其他城市暂提供地图入口。旧交通条目保留在数据库中，不再以列表展示。
- 升级到 v3 会保留用户、行程和已有条目，将已有上海景点/美食攻略改为小红书搜索，并一次性补充新增内容；请先备份。

## 数据与协作规则

- 页面内容是共享数据，不以浏览器本地存储作为事实来源。
- 项目口令用于控制谁能进入整个项目；浏览器通过后会保存独立的项目访问 Cookie。
- ID 仅用于署名，不是账号或密码。进入项目的同行者可以选择任意已有 ID，也可以创建新 ID。
- 同一 ID 可以在多个设备使用；切换用户不会让其他设备退出。
- 普通同行者的内容编辑权限相同；项目、城市与用户管理仅限管理员。删除操作需要二次确认，不提供页面内恢复。
- 如部署在公网，设置足够长且不可猜的 `TRIP_PROJECT_CODE`、固定 `TRIP_PUBLIC_ORIGIN` 并启用 HTTPS。
- 只运行一个后端进程。SQLite 的 WAL 模式可支持小团队并发，但不适合多机或多进程写入。

## 测试

```sh
python3 -m unittest discover -s tests -v
```

测试会使用临时数据库，不会修改 `data/trip.db`。

## 备份

不要在服务运行时只复制 `.db` 文件，因为最新事务可能仍在 WAL 中。使用 Python 的 SQLite 在线备份：

```sh
python3 server.py --data-dir /var/lib/shanghai-trip \
  --backup /var/backups/shanghai-trip/trip-2026-09-08-2100.db
```

备份模式不会创建缺失的源数据库，也不会覆盖同名备份；完成完整性检查后才会原子写入目标文件。

部署到 Linux 小服务器的完整步骤见 [DEPLOY.md](DEPLOY.md)。

已有 Docker 和宝塔 Nginx 的阿里云服务器，见 [Docker 部署与更新说明](DOCKER_DEPLOY.md)。

## 初始资料来源

初始景点、美食和交通信息以官方或权威页面为主，包括：

- [上海市人民政府 Citywalk](https://www.shanghai.gov.cn/citywalk/)
- [上海市交通委员会轨道交通](https://jtw.sh.gov.cn/csgdjt/index.html)
- [上海博物馆东馆参观服务](https://www.shanghaimuseum.cn/mu/frontend/pg/m/service/visit-east)
- [上海自然博物馆](https://www.snhm.org.cn/)
- [上海迪士尼轨道交通指南](https://www.shanghaidisneyresort.com/zh-cn/experience/guest-service/rail)
- [上海文旅推广网](https://www.meet-in-shanghai.net/)

开放时间、票价、预约与交通运营信息会变化，页面文案仅用于规划，出发前请通过条目外链核对官方公告。

线路图采用 [Yveltal 的 Shanghai Metro Linemap](https://commons.wikimedia.org/wiki/File:Shanghai_Metro_Linemap.svg)，2025-12-27 版本，按 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 再分发 PNG 预览，未改图。它不是实时运营图，页面另提供上海地铁官方链接。新增资料参考[上海政府黄浦区介绍](https://www.shanghai.gov.cn/huangpu/index.html)、[虹口区介绍](https://www.shanghai.gov.cn/hongkou/index.html)及[本地非遗美食介绍](https://www.shanghai.gov.cn/nw17239/20260109/a971cbda0bb246e4a6666720bcbf16b3.html)。
