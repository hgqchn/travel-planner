# 部署到一台 Linux 小服务器

推荐组合：**Python 3.9+ + systemd + Caddy + SQLite**。应用仅监听服务器本机的 `127.0.0.1:8000`，Caddy 对外提供 HTTPS。

以下示例适用于 Debian / Ubuntu。域名示例为 `trip.example.com`，请替换成你自己的域名。

## 1. 准备域名和系统

在域名服务商处添加 A 记录（如有 IPv6 再加 AAAA），指向服务器公网 IP。确认安全组或防火墙允许 TCP 端口 22、80、443。

在服务器安装运行环境：

```sh
sudo apt update
sudo apt install -y python3 caddy
command -v python3
python3 --version
python3 -c "import sys; print(sys.executable)"
```

确认版本不低于 3.9。项目没有 Python 第三方包，也不需要 Node.js、npm 或数据库服务。

## 2. 创建独立用户和目录

```sh
sudo useradd --system --home /var/lib/shanghai-trip --shell /usr/sbin/nologin shanghai-trip
sudo install -d -m 755 -o root -g root /opt/shanghai-trip /opt/shanghai-trip/releases
sudo install -d -m 700 -o shanghai-trip -g shanghai-trip /var/lib/shanghai-trip /var/backups/shanghai-trip
```

从本机项目目录上传代码：

```sh
rsync -av --exclude 'data/' --exclude '__pycache__/' ./ user@your-server:/tmp/shanghai-trip-release/
```

登录服务器后安装本次版本。每次都使用一个新的发布目录，再原子切换 `current` 软链接，避免覆盖正在运行的代码：

```sh
release_name=20260908-2100
sudo install -d -m 755 -o root -g root "/opt/shanghai-trip/releases/$release_name"
sudo cp -R /tmp/shanghai-trip-release/. "/opt/shanghai-trip/releases/$release_name/"
sudo chown -R root:root "/opt/shanghai-trip/releases/$release_name"
sudo ln -s "/opt/shanghai-trip/releases/$release_name" /opt/shanghai-trip/current.next
sudo mv -Tf /opt/shanghai-trip/current.next /opt/shanghai-trip/current
```

后续更新代码仍需排除 `data/`；生产数据库固定放在 `/var/lib/shanghai-trip`，不会被发布目录覆盖。

## 3. 设置项目口令

服务必须配置项目口令。创建只允许 root 读取的环境文件：

```sh
sudo install -m 600 -o root -g root /dev/null /etc/shanghai-trip.env
sudoedit /etc/shanghai-trip.env
```

写入以下三行，并把域名替换为实际使用的 HTTPS 地址：

```text
TRIP_PROJECT_CODE=替换成一个只发给同行者的长项目口令
TRIP_ADMIN_PASSWORD=替换成独立管理员密码
TRIP_PUBLIC_ORIGIN=https://trip.example.com
```

项目口令在每台设备进入整个项目时验证，数据库仅保存加盐哈希，不保存明文。`TRIP_PROJECT_CODE` 仅用于初次建库或旧版本升级；之后在 `/admin.html` 修改项目口令，重启不会被环境变量覆盖。管理员密码仅在服务端环境中配置，更换后重启生效，不能发给普通同行者。验证项目口令后，同行者可无密码选择或新建用户 ID。`TRIP_PUBLIC_ORIGIN` 会让写接口只接受来自该网页的浏览器请求。

## 4. 配置 systemd

先核对 `deploy/shanghai-trip.service.example` 中的 `ExecStart` 是否使用第 1 步确认的 Python 绝对路径，然后安装服务：

```sh
sudo cp /opt/shanghai-trip/current/deploy/shanghai-trip.service.example /etc/systemd/system/shanghai-trip.service
sudo systemctl daemon-reload
sudo systemctl enable --now shanghai-trip
sudo systemctl status shanghai-trip --no-pager
curl -fsS http://127.0.0.1:8000/api/health
```

服务以非 root 用户运行，只能写入 `/var/lib/shanghai-trip`，异常退出后会自动重启。不要为 SQLite 启动多个 worker 或多个应用实例。

## 5. 配置域名和 HTTPS

如果服务器还没有 Caddy 配置文件，可先复制示例并修改第一行域名：

```sh
sudo cp /opt/shanghai-trip/current/deploy/Caddyfile.example /etc/caddy/Caddyfile
sudoedit /etc/caddy/Caddyfile
```

如果已有 Caddy 配置，跳过上面的 `cp`，只用 `sudoedit` 把示例中的 `trip.example.com { ... }` 站点块合并进去，不要覆盖其他站点。然后验证并重载：

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

域名解析正确且 80/443 端口可访问后，Caddy 会自动申请和续期 HTTPS 证书。最后检查：

```sh
curl -fsS https://trip.example.com/api/health
```

然后用手机打开该地址，输入项目口令、创建第一个用户 ID，并新增一条行程，再用第二个浏览器确认约 5 秒内能看到更新。还应测试从用户 A 切换到用户 B，再切回 A。

## 6. 更新与回滚

每次更新前先创建一致性备份：

```sh
backup_name="before-update-$(date +%Y%m%d-%H%M%S).db"
sudo -u shanghai-trip /usr/bin/python3 /opt/shanghai-trip/current/server.py \
  --data-dir /var/lib/shanghai-trip \
  --backup "/var/backups/shanghai-trip/$backup_name"
```

按第 2 步上传到新的版本目录并切换 `current` 后执行：

```sh
sudo systemctl restart shanghai-trip
curl -fsS http://127.0.0.1:8000/api/health
```

如果新版本检查失败，且没有数据库结构升级，可把 `current` 原子切回上一个发布目录并重启服务（将目录名替换为实际上一版本）。v3 含结构升级，不能只回滚代码：须先停止服务、保留当前数据库和 WAL/SHM 文件，再从升级前备份恢复匹配旧版本的数据库；升级后的修改不会出现在旧备份中。

```sh
sudo ln -s /opt/shanghai-trip/releases/上一版本目录 /opt/shanghai-trip/current.next
sudo mv -Tf /opt/shanghai-trip/current.next /opt/shanghai-trip/current
sudo systemctl restart shanghai-trip
curl -fsS http://127.0.0.1:8000/api/health
```

备份命令会拒绝缺失的源库或已存在的目标文件，并在原子落盘前执行完整性检查。建议每日备份并保留多个日期版本，同时把至少一份备份同步到另一台机器或对象存储。应定期把备份恢复到临时目录并执行 SQLite `PRAGMA integrity_check`，确认备份真正可用。

## 资源预估

小规模同行群组通常只需 1 核 CPU、512 MB–1 GB 内存和少量磁盘。主要磁盘增长来自 SQLite 修改记录与备份；线路图仅在交通页延迟加载，浏览器无需下载框架运行时。
