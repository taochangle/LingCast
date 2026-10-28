# 成都服务器部署（2026-10-28）

## 当前版本发布方式（取代迁移时的构建方式）

本地修改、测试后提交并推送 main；线上工作目录同为 Git 仓库，执行 `git pull --ff-only origin main`，再 `bash deploy/chengdu.sh`。脚本拒绝已修改的跟踪文件，从同一提交构建 API 和前端，并在镜像标签 `org.opencontainers.image.revision` 与 `runtime/deployed-revision` 记录提交号。构建全部通过后才切换服务并重启 Worker。

部署入口已改为版本管理内的根 docker-compose.yml + deploy/chengdu.override.yml，项目名和数据卷继续用 lingcast-chengdu；私有 `.env`、worker/.env.local、模型 resident.env、数据库及素材不进 Git。迁移时的私有 compose.json 留作回退备份，后续不要混用它部署。模型服务源码也从当前 Git 提交安装，而非从本地手工传文件。

本次口播分段：后端提交一个完整讲稿任务，IndexTTS 整段配音一次；Worker 用静音检测寻找自然停顿，目标每段约 2–4 秒，优先保证不切断词语。无停顿的长句会保留到下一处停顿（最长 18 秒），无法安全切分则报错要求增加标点。PCM 片段首尾相接、样本不丢失；每段 LatentSync 完成立即进入播放队列，同时后台继续下一段。播放时裁去模型人为添加的首尾静音。生成速度尚慢于播放，片段间仍可能回到待机。

当前直播系统运行于 `chengdu-taoxin`（127.0.0.1），目录 `/data1/taoxin/LingCast`，后台 `http://127.0.0.1:59480`。本机原实例已停止，数据卷和源码保留；恢复本机前先停远程 Worker，避免两套同时处理/推流。

## 网络

本次部署仅对外发布 59480。MariaDB 不发布宿主机端口。Redis 127.0.0.1:59479、RustFS 127.0.0.1:59478、SRS RTMP 127.0.0.1:59481 供原生 Worker 使用；模型服务 127.0.0.1:59411/59412。浏览器素材和 HTTP-FLV 都经 59480 的 nginx 网关。

Qwen 已在现有 `vllm` 容器中。api-admin/api-live 加入其外部网络 `vllm_default`，使用 `http://vllm:8000/v1`，不绕公网 IP 或宿主机 18003。其他应用已有端口不在本次迁移变更范围。

## 运行与更新

服务器私有 `compose.json` 是部署入口，内含展开的凭据，权限 600，不入 Git。Compose 项目名 `lingcast-chengdu`，使用独立数据卷，不覆盖服务器其他项目。API 为本机交叉编译的 Linux amd64 二进制，管理端为编译后的静态资源；部署 Dockerfile.api/Dockerfile.admin 基于服务器既有 nginx:alpine 镜像构建。

```
cd /data1/taoxin/LingCast
docker compose -f compose.json ps
docker compose -f compose.json up -d
systemctl --user status lingcast-indextts lingcast-latentsync lingcast-stream
journalctl --user -u lingcast-stream -f
```

模型仍使用既有 `/data1/taoxin/index-tts/.venv` 和 `/data1/taoxin/avatar/.venv-latentsync`，服务源码/私有环境文件在 `/data1/taoxin/lingcast-resident`；S3 白名单已改成 127.0.0.1:59478。原生流 Worker 用独立 `resident-runtime` 依赖组，不重复安装 GPU 模型。用户 systemd 配置在 `~/.config/systemd/user/lingcast-*.service`，已启用 Linger，可开机启动；Worker 启动前等待 API 和模型 ready，最长 300 秒。Docker 服务设置 unless-stopped。

## 备份和恢复

本地迁移备份：`/Users/taoxin/虚拟直播/backups/lingcast-20261028`；远程副本：`/data1/taoxin/LingCast/migration-backup`。

- database.sql：冻结写入后的 MariaDB 逻辑备份。
- objects/ 与 objects-manifest.json：44 个 S3 对象及 SHA256；远程逐文件哈希已校验。
- dump.rdb 与 redis-records.json：Redis 快照与应用级 DUMP/RESTORE 备份，包含持久配置和原有过期时间。
- source-config.tar.gz：源码与原配置，不含可重新安装的虚拟环境、模型权重和 node_modules。模型权重原本已在服务器，不需要来回复制。
- verification.txt：数据库关键表计数核对。部署支持包另存远程 Compose、服务配置与当前 Worker 环境文件。

注意：空 AOF 目录下直接启用 appendonly 不一定导入现有 dump.rdb。本次额外通过 Redis RESTORE REPLACE 导入记录，校验所有永久值并执行 BGREWRITEAOF，确保配置保留。

回退本机：先在远程停止 lingcast-stream，并停止对外推流；再从原本的本机项目目录启动 Docker 基础服务、API 和管理端，恢复 resident.env 的旧 S3 端点并重建 SSH 转发，最后运行本地 Worker。本地原数据卷没有删除；若回退到迁移快照，可用 SQL、S3 对象和 Redis 记录备份恢复。切勿同时恢复两侧 Worker。

短句验证：迁移后「大家好，欢迎来到直播间。」提交至第一帧说话约 18.54 秒，生成约 17.81 秒，22.55 秒时播放完并返回待机。此为特定短句单次测量，不代表所有输入的延迟，也不是严格迁移前后性能对照。
