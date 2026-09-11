# 阿里云 Docker 部署

本部署复用宝塔安装的宿主机 Nginx，不启动第二个 Nginx 容器。

2026-09-10 已完成多项目版镜像构建、上线健康检查和集合备份；本机公网 8081 健康接口返回 HTTP 200。服务器直连 GitHub 超时时，使用下文的 Git bundle 方式传输已推送的提交。

服务器测试解释器为 `/usr/bin/python3`（12 项测试通过），应用容器解释器为 `/usr/local/bin/python`。首次镜像通过 `public.ecr.aws/docker/library/python:3.12-slim-bookworm` 拉取后标记为 `python:3.12-slim-bookworm`，未更改整台服务器的镜像加速配置。

- 旅行网站：`http://39.106.229.7:8081`
- 管理入口：`http://39.106.229.7:8081/admin.html`
- 原网站：`http://39.106.229.7`，仍转发到 `127.0.0.1:8080`
- 旅行容器：Compose 项目 `travel-planner`，只绑定 `127.0.0.1:18081`
- Nginx 新站点：`/www/server/panel/vhost/nginx/travel-planner.conf`

只有 IP 时先使用 HTTP；口令和内容传输未加密。绑定域名并启用 HTTPS 后，更新 `/etc/travel-planner/app.env` 中的 `TRIP_PUBLIC_ORIGIN`，设置 `TRIP_COOKIE_SECURE=1`，重建应用容器。

## 数据与配置

| 服务器路径 | 用途 |
| --- | --- |
| `/opt/travel-planner/repo` | GitHub 仓库，只用于获取代码 |
| `/opt/travel-planner/releases` | 按 Git 提交导出的发布目录 |
| `/opt/travel-planner/deployment` | 当前生效的 Dockerfile、Compose 和运维脚本 |
| `/opt/travel-planner/current.env` | 当前镜像版本 |
| `/opt/travel-planner/previous.env` | 上一个成功版本的镜像标签 |
| `/etc/travel-planner/app.env` | 随机生成的初始项目口令、管理员密码及访问来源，仅 root 可读 |
| `/var/lib/travel-planner` | 持久化 SQLite 数据，由容器 UID 10001 写入 |
| `/var/lib/travel-planner/project-*.db` | 各独立项目数据库 |
| `/var/lib/travel-planner/place_cache.db` | 同一部署共享的景点、美食资料缓存 |
| `/var/lib/travel-planner/metro_maps` | 同一部署共享的在线线路图更新文件与元数据 |
| `/var/backups/travel-planner` | SQLite 一致性备份 |

在服务器 root 终端执行 `cat /etc/travel-planner/app.env` 可查看登录信息；不要发到 GitHub。`TRIP_PROJECT_CODE` 为初始项目口令，后台修改后以数据库为准；`TRIP_ADMIN_PASSWORD` 为管理员密码。

## 更新应用

在本机提交并推送应用代码到 GitHub 后，在服务器 root 终端执行：

```bash
/opt/travel-planner/deployment/update.sh
```

默认部署 GitHub 默认分支。也可以传入标签或提交 ID：

```bash
/opt/travel-planner/deployment/update.sh origin/main
```

服务器无法直连 GitHub 时，可在已经推送的本地仓库执行 `git bundle create /tmp/travel-planner.bundle main`，通过 Workbench 上传到服务器，随后运行 `TRIP_GIT_BUNDLE=/tmp/travel-planner.bundle /opt/travel-planner/deployment/update.sh origin/main`。脚本验证并导入 Git 对象，仍按提交构建、备份和验收；不会更改 GitHub 远程地址。请仅上传已核对并推送的提交，完成后按需移走临时 bundle。

脚本使用独占锁，依次获取代码、构建带提交标识的镜像、检查镜像内模块导入和 JSON 资源、在线备份数据库、重建容器并等待健康检查。构建或镜像预检失败不会替换正在运行的容器。重建阶段会短暂中断。

本次 Docker 配置由本机单独上传到 `/opt/travel-planner/deployment`；应用更新不会自动替换这套运维配置。修改 Dockerfile、Compose 或脚本后，需要显式同步该目录再发布，避免仓库变更意外改动生产挂载和端口。

本版本上线前先备份运维目录，再从同一提交同步 `Dockerfile`、`deploy/update.sh` 和 `deploy/backup.sh`（后两个在运维目录中保留为 `update.sh`、`backup.sh`，权限 755）。保留现有 Compose 的端口、挂载和资源限制。首次从旧镜像升级时备份仍由旧镜像执行；成功升级后再运行一次备份，确认新的多数据库集合备份可用。不要复制本机 `data/` 或用配置示例覆盖服务器现有口令。

### 城市与 AI 升级注意事项

2026-09-10 的功能升级先在本地验收，上线前必须将本版本 `Dockerfile` 同步到运维目录；它新增了 `ai_service.py`、`city_catalog.py` 和 `city_catalog.json` 的复制，否则镜像缺少运行文件。前端 SVG 随 `public` 目录进入镜像。

多项目与缓存版还新增 `place_cache.py`、`project_store.py` 和 `backup_all.py`。本次需同时同步 `Dockerfile` 和 `deploy/backup.sh` 到运维目录。新版备份脚本会备份默认项目、注册的全部子项目和共享缓存；运行中的旧镜像无集合备份脚本时，自动使用原单库备份。

在服务器 `/etc/travel-planner/app.env` 增加 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_MODEL=deepseek-v4-flash-vision-exp`（示例见 `deploy/app.env.example`）。配置文件保持仅 root 可读，不提交 Git、不打包进镜像。没有 Key 时 AI 入口显示未配置，城市和其他功能仍可使用。

备份后执行更新脚本，默认城市会自动完成一次性补齐，保留原有内容。生成请求提交后立即返回任务 ID，浏览器轮询进度，Nginx 无需为了 AI 调整为长连接超时。仍只运行一个应用进程，以统一管理队列与调用额度。

镜像和发布目录不会自动删除。确认不再需要回滚后按版本清理，避免对整台服务器执行全局 Docker 清理而影响其他项目。

### 高清线路图升级与更新

线路图版本还需将包含 `metro_maps.py`、`metro_sources.json` 的新 `Dockerfile` 同步到运维目录。内置图片与来源元数据位于仓库 `public/assets/metro`，随 `public` 复制进镜像，启动和查看已下载图不依赖外网。

用户在交通页点击“检查并更新线路图”后，由服务器连接 Wikimedia Commons 检查指定源文件；下载结果写入容器 `/data/metro_maps`，对应宿主机 `/var/lib/travel-planner/metro_maps`。已有数据挂载可直接持久化该目录，无需新增宝塔网站、端口或入站规则；目录沿用应用 UID 10001 的写入权限。各项目共用更新文件，重建容器后仍可读取。

如果服务器无法连接来源站，更新会提示失败并保留原图；可恢复出站网络后重试。按钮只检查 `metro_sources.json` 中登记的文件，源文件换名、迁移或需要改用另一张图时，应修改图源目录并重新发布代码。页面展示的是社区文件修订时间，运营信息仍以官方公告为准。

SQLite 集合备份不包含线路图文件。内置图可从对应镜像恢复；需要保留在线下载的版本和来源记录时，应另行备份宿主机 `metro_maps` 整个目录。

## 查看状态与日志

### 每日计划与高德地图版本

路线保存和行程页预览版本新增 `route_image.py`，发布前需同步仓库 Dockerfile 到运维目录，确保路线图片模块进入镜像。启动会自动创建各项目的 `day_routes` 表；现有数据库和地图配置沿用。发布脚本仍执行集合备份、镜像预检和健康检查。

发布本版本时需同步仓库 `Dockerfile` 到 `/opt/travel-planner/deployment/Dockerfile`，新增每日规划、省市目录、高德和景区别名模块及资源必须一同进入镜像。保留现有 Compose 端口和数据挂载；在 `/etc/travel-planner/app.env` 配置 `AMAP_JS_KEY`、`AMAP_SECURITY_JS_CODE`、`AMAP_WEB_SERVICE_KEY` 和 `AMAP_DAILY_LIMIT`，文件权限保持 600。不要将本地密钥文件放入发布目录。

现有整站 Nginx 代理会将 `/_AMapService/` 转发给后端，无需额外公开端口。高德控制台若设置了域名或 IP 白名单，需要包含实际网站和服务器出口。更新前执行集合备份，确认没有进行中的 AI 任务；新版本启动时为各项目补齐每日计划结构，并保留旧行程。涉及数据库结构升级时，回滚必须配套升级前的数据备份。

```bash
docker compose --env-file /opt/travel-planner/current.env \
  -f /opt/travel-planner/deployment/compose.yaml ps

docker compose --env-file /opt/travel-planner/current.env \
  -f /opt/travel-planner/deployment/compose.yaml logs --tail 100 app

curl -fsS http://127.0.0.1:18081/api/health
curl -fsS http://39.106.229.7:8081/api/health
```

部署失败时先检查日志；候选版本记录在 `/opt/travel-planner/candidate.env`。`current.env` 仅在健康检查成功后更新，因此失败时它可能仍指向旧版本。

## 备份与回滚

手动备份：

```bash
/opt/travel-planner/deployment/backup.sh
systemctl list-timers travel-planner-backup.timer
```

定时器每天北京时间 03:30 左右在线备份。备份暂不自动删除，需要定期下载到服务器以外并检查磁盘；更新前也会额外备份。服务运行时不能只复制 `trip.db`，因为最新事务可能仍在 WAL 中。

新版本备份输出为 `trip-时间戳/` 目录，内含多份经过 SQLite 完整性检查的数据库。恢复时应保留整个目录中的项目库及 `place_cache.db`，不能仅恢复默认 `trip.db`；各项目备份时间相近但并非跨库同一时刻快照。

回滚前检查新旧版本的数据库兼容性。无结构升级时可以使用 `previous.env` 的镜像重建；有结构升级时，应先停服务、保留当前数据库及 WAL/SHM，再恢复升级前的匹配备份。恢复旧备份会丢失备份之后的修改，因此脚本不会自动回滚数据库。

## 阿里云与宝塔面板

阿里云 ECS → 实例 → 安全组 → 入方向：允许 **TCP 8081**。需要供同行者从任意网络访问时，来源设为 `0.0.0.0/0`；不要放行容器内部端口 18081。

宝塔“安全”中的系统防火墙也需允许 TCP 8081。部署过程中通过 UFW 添加此规则后，无需重复添加。

新 Nginx 配置文件由命令行安装，可能不会显示为宝塔“网站”列表中的独立站点；可在文件管理中编辑该文件。不要另建一个监听相同 IP 和端口的重复站点。

Nginx 修改后先验证再平滑重载：

```bash
/www/server/nginx/sbin/nginx -t
/www/server/nginx/sbin/nginx -s reload
```

后端仍是项目自带 Python 标准库 HTTP 服务，本配置面向小规模同行者协作，不是多实例高并发架构。

## AI 限制调整的发布要求

本版本取消 AI 生成间隔、未完成任务与队列数量、规划天数和生成条目数的业务上限。发布时还需将旅行站点 Nginx 的 `client_max_body_size` 从 `64k` 调整为 `8m`，执行 `nginx -t` 后重载，以匹配较大 AI 结果的导入。普通业务接口在后端继续使用原请求大小限制。主页支持凭唯一项目口令直接进入对应项目；原有项目分享链接仍可使用。
