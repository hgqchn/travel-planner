# 阿里云 Docker 部署

本部署复用宝塔安装的宿主机 Nginx，不启动第二个 Nginx 容器。

2026-09-09 已完成服务器部署、登录验证、备份完整性检查和一次更新演练；原网站返回 HTTP 200。本机公网 8081 检查尚未成功，需要确认阿里云安全组放行后再验收公网访问。

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

脚本使用独占锁，依次获取代码、构建带提交标识的镜像、在线备份数据库、重建容器并等待健康检查。构建失败不会替换正在运行的容器。重建阶段会短暂中断。

本次 Docker 配置由本机单独上传到 `/opt/travel-planner/deployment`；应用更新不会自动替换这套运维配置。修改 Dockerfile、Compose 或脚本后，需要显式同步该目录再发布，避免仓库变更意外改动生产挂载和端口。

镜像和发布目录不会自动删除。确认不再需要回滚后按版本清理，避免对整台服务器执行全局 Docker 清理而影响其他项目。

## 查看状态与日志

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
