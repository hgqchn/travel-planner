# 项目与发布说明

## 项目定位

本项目是 Web 端 AI 行程规划应用。以根目录 README.md 为当前功能和使用说明的主入口；部署细节见 DOCKER_DEPLOY.md，地图配置见 AMAP_SETUP.md。docs/archive/ 仅为历史记录。

## 工作原则

- 修改前检查 Git 状态并阅读相关代码；保留用户未提交的工作。
- 不修改或清空本地、服务器的旅行数据和密钥。data/、*.env、deepseek.md、高德地图.md 不得进入 Git 或镜像；不要输出密钥内容。
- 当前默认项目 main 已由用户删除，必须保持删除状态，不要在发布时恢复。
- 用户已选择在 Codex 内部终端运行测试，无需重复询问。
- 使用 Python 前确认实际解释器路径；本机已验证 /Library/Developer/CommandLineTools/usr/bin/python3，服务器为 /usr/bin/python3，容器为 /usr/local/bin/python。更换环境时重新确认。
- 本机 Node.js 由 ~/.nvm 管理，非交互 shell 先加载 NVM，再 nvm use default；存在 .nvmrc 时优先遵循它，不另装 Node.js。
- 用户要求“更新 GitHub 和服务器”或在连续发布语境中说“更新吧”时，完成提交、推送、备份、部署和验收。只要求更新服务器时，可提交本地版本并通过 bundle 部署，不擅自声称已推送 GitHub。
- 普通授权发布无需额外询问；遇到无法安全解决的冲突、数据风险或缺少必要权限时说明具体原因。

## 发布前检查

1. 检查 git status、提交历史、差异与 git diff --check，确认本次发布范围。
2. 阅读 README、相关配置、服务入口。新增后端模块或资源时同步检查 Dockerfile 的 COPY 列表，防止本机正常但镜像缺文件。
3. 检查待提交文件不含凭证、数据库和临时文件；不要使用会打印密钥的扫描方式。
4. 运行与改动匹配的测试。仅前端改动可运行全部前端测试；后端、接口、导出或数据相关改动运行后端回归，并按影响运行前端回归：

   ```sh
   python3 -B -m unittest discover -s tests
   node --test tests/*.cjs
   ```

   使用已确认的 Python 绝对路径。后端测试监听本地临时端口；若沙箱拦截，申请相应执行权限，不把权限错误当作代码回归。测试失败须定位并解决，不能只依据 shell 最后一条 tail 命令的退出码判断成功。
5. 确认服务器当前提交、容器健康状态和所有项目 ai_jobs 表中 queued/running 任务数量。存在进行中的 AI 任务时先等待，避免重启中断生成。

## GitHub

- 仓库：https://github.com/hgqchn/travel-planner
- SSH remote：git@github.com:hgqchn/travel-planner.git
- 当前发布分支：main；先核对实际分支，不强制覆盖远端。
- 提交本次改动并推送；确认 push 成功。Git 写入及网络操作按运行环境申请权限。
- 完成后检查工作区和远端同步状态，不覆盖工作期间新增的用户改动。

## 阿里云连接

连接文档位于上级目录：../阿里云服务器连接指南.md。发布时优先复用既有 Workbench 配置，不重新安装或配置凭证。

- CLI：/Users/houguoqing/.local/bin/workbench
- profile：default
- region：cn-beijing（必须显式传入）
- instance：i-2zeit1ayv43tc74ezjks
- user：root
- 凭证文件：~/.workbench/config.json，禁止打印。

远程执行模板：

```sh
/Users/houguoqing/.local/bin/workbench exec \
  --profile default --region cn-beijing \
  --instance-id i-2zeit1ayv43tc74ezjks --user-name root \
  --timeout 240 --command '待执行的远程脚本'
```

部署需显式设置 --timeout 240，默认超时可能在构建过程中结束。各次 exec 不共享目录和环境。多行命令按 shell 规则安全引用；不要用 JSON.stringify 代替 shell 转义。

Workbench 通过标准输入发送脚本。无需读取输入的 docker compose exec -T 等命令必须使用 </dev/null，避免吞掉后续命令；有意向容器传入 Python 脚本时另行处理。

## 服务器布局

- /opt/travel-planner/repo：获取提交的 Git 仓库。
- /opt/travel-planner/releases：按提交导出的镜像构建目录。
- /opt/travel-planner/deployment：实际生效的 Dockerfile、compose.yaml、update.sh、backup.sh。
- /opt/travel-planner/current.commit、current.env、previous.env：当前提交及镜像版本。
- /opt/travel-planner/ops-backups：发布前运维配置备份。
- /etc/travel-planner/app.env：环境配置，保留已有值，禁止用本机示例覆盖。
- /var/lib/travel-planner：持久化数据目录。
- /var/lib/travel-planner/api-settings.json：后台保存的密钥配置，优先于对应环境变量；存在时单独安全备份。
- /var/backups/travel-planner：数据库集合备份。
- Compose 项目 travel-planner，容器 travel-planner-app-1。
- 容器通过 127.0.0.1:18081 提供服务；宿主机 Nginx 对外端口 8081。
- 应用：http://39.106.229.7:8081；管理入口 /admin.html。
- 原网站：http://39.106.229.7，发布旅行应用时保持其服务和 Nginx 配置。

## 部署步骤

1. 记录服务器 current.commit，作为 bundle 基线；版本号、时间戳和校验值每次动态获取，禁止照搬历史值。
2. 服务器直连 GitHub 可能超时。可从本地生成增量 Git bundle，上传后核对 SHA-256：

   ```sh
   git bundle create /tmp/travel-release.bundle main ^服务器当前提交
   shasum -a 256 /tmp/travel-release.bundle
   /Users/houguoqing/.local/bin/workbench upload \
     /tmp/travel-release.bundle /tmp/travel-release.bundle \
     --profile default --region cn-beijing \
     --instance-id i-2zeit1ayv43tc74ezjks --user-name root
   ```

   服务器当前提交必须是本地已知的有效基线，否则先调查或使用完整 bundle。涉及 GitHub 发布时先确认提交已推送。
3. 在 /opt/travel-planner/ops-backups 下新建权限 0700 的时间戳目录，备份 deployment、current.env、current.commit；如存在 api-settings.json，也保留权限复制到该目录。不输出文件内密钥。
4. 运维目录不会自动随应用提交更新。Dockerfile 或部署脚本有变化时，先从同一目标提交提取并同步对应文件，保留脚本执行权限。Compose 端口、挂载和资源限制只在确有需要时修改。
5. 执行现有发布脚本，指定确切提交：

   ```sh
   TRIP_GIT_BUNDLE=/tmp/travel-release.bundle \
     /opt/travel-planner/deployment/update.sh 目标提交
   ```

   脚本持有部署锁，构建镜像，预检模块与 JSON，备份全部项目数据库及共享缓存，再重建容器并等待健康检查。不可跳过备份或仅替换容器内文件。
6. 等待命令最终成功，记录实际镜像版本和备份目录。失败时检查构建或容器日志；若数据库可能已迁移，不自动回退旧代码或恢复数据库覆盖新数据。

## 上线验收

- current.commit 与目标提交一致，容器 healthy。
- 对本次集合备份及线上数据库执行 SQLite integrity_check；核对备份中的 users、items ID 在对应线上数据库中仍存在，避免输出旅行内容。
- 默认项目 main 仍为删除状态。
- 核对本次修改的线上 JS/CSS 文件 SHA-256 与本地提交一致，避免缓存或镜像遗漏。
- 本机外网访问 /api/health 成功，服务器首页、后台页面及原网站可访问；按改动验证新接口的权限和响应。
- 涉及 AI/高德配置时只输出是否配置等布尔状态；不把未实际执行的付费 AI 生成或地图功能称为端到端验证通过。
- 最终用中文简述提交、GitHub/服务器结果、测试、备份和验收；如使用 Python，列出本次解释器绝对路径。
