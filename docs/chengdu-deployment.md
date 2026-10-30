# 成都服务器部署（2026-10-29 更新）

## 当前版本发布方式（取代迁移时的构建方式）

本地修改、测试后提交并推送 main；线上工作目录同为 Git 仓库，执行 `git pull --ff-only origin main`，再 `bash deploy/chengdu.sh`。脚本拒绝已修改的跟踪文件，从同一提交构建 API 和前端，并在镜像标签 `org.opencontainers.image.revision` 与 `runtime/deployed-revision` 记录提交号。构建全部通过后才切换服务并重启 Worker。

部署入口已改为版本管理内的根 docker-compose.yml + deploy/chengdu.override.yml，项目名和数据卷继续用 lingcast-chengdu；私有 `.env`、worker/.env.local、模型 resident.env、数据库及素材不进 Git。迁移时的私有 compose.json 留作回退备份，后续不要混用它部署。模型服务源码也从当前 Git 提交安装，而非从本地手工传文件。

当前直播链路为 Qwen SSE → CosyVoice音频流 → MuseTalk逐帧 → FFmpeg/SRS；手动口播按原文生成。优先级为手动口播 → 评论回复 → 欢迎 → 自主主持，评论和欢迎分别排队。常驻配置、素材映射、缓存与回滚见 [流式直播链路](cosyvoice-musetalk-live.md)。IndexTTS/LatentSync 保留作旧模式和回滚使用。

真实公网origin仅保存在服务器私有 `.env` 的 `LINGCAST_PUBLIC_ORIGIN`，不写入仓库。宿主机本地调试可访问 `http://127.0.0.1:59480`。本机原实例已停止，数据卷和源码保留；恢复本机前先停远程 Worker，避免两套同时处理/推流。

## 网络

本次部署仅对外发布 59480。MariaDB 不发布宿主机端口。Redis 127.0.0.1:59479、RustFS 127.0.0.1:59478、SRS RTMP 127.0.0.1:59481 供原生 Worker 使用；当前CosyVoice/MuseTalk模型服务 127.0.0.1:59495/59496，旧模型仍为127.0.0.1:59411/59412。浏览器素材和 HTTP-FLV 都经 59480 的 nginx 网关。

Qwen 已在现有 `vllm` 容器中。api-admin/api-live 加入其外部网络 `vllm_default`，使用 `http://vllm:8000/v1`，不绕公网 IP 或宿主机 18003。其他应用已有端口不在本次迁移变更范围。

## 运行与更新

当前使用仓库根Compose和成都override；不再执行私有compose.json。

```bash
cd /data1/taoxin/LingCast
git pull --ff-only origin main
bash deploy/chengdu.sh
docker compose -p lingcast-chengdu -f docker-compose.yml -f deploy/chengdu.override.yml ps
systemctl --user status lingcast-cosyvoice lingcast-musetalk lingcast-stream
journalctl --user -u lingcast-stream -f
cat runtime/deployed-revision
```

CosyVoice和MuseTalk使用各自独立模型环境，原生Worker使用 `resident-runtime` 依赖组。systemd用户单元位于 `~/.config/systemd/user/`，已启用Linger。启动等待模型及API ready，当前等待上限300秒；首次冷启动超时的处理见流式链路文档，不能仅凭进程active判断预热完成。数据库、素材、Redis配置和采集密钥在发布时保留。

## 备份和恢复

本地迁移备份：`/Users/taoxin/虚拟直播/backups/lingcast-20261028`；远程副本：`/data1/taoxin/LingCast/migration-backup`。

- database.sql：冻结写入后的 MariaDB 逻辑备份。
- objects/ 与 objects-manifest.json：44 个 S3 对象及 SHA256；远程逐文件哈希已校验。
- dump.rdb 与 redis-records.json：Redis 快照与应用级 DUMP/RESTORE 备份，包含持久配置和原有过期时间。
- source-config.tar.gz：源码与原配置，不含可重新安装的虚拟环境、模型权重和 node_modules。模型权重原本已在服务器，不需要来回复制。
- verification.txt：数据库关键表计数核对。部署支持包另存远程 Compose、服务配置与当前 Worker 环境文件。

注意：空 AOF 目录下直接启用 appendonly 不一定导入现有 dump.rdb。本次额外通过 Redis RESTORE REPLACE 导入记录，校验所有永久值并执行 BGREWRITEAOF，确保配置保留。

回退本机：先在远程停止 lingcast-stream，并停止对外推流；再从原本的本机项目目录启动 Docker 基础服务、API 和管理端，恢复 resident.env 的旧 S3 端点并重建 SSH 转发，最后运行本地 Worker。本地原数据卷没有删除；若回退到迁移快照，可用 SQL、S3 对象和 Redis 记录备份恢复。切勿同时恢复两侧 Worker。

历史 IndexTTS/LatentSync 短句验证（不代表当前流式模式）：迁移后「大家好，欢迎来到直播间。」提交至第一帧说话约 18.54 秒，生成约 17.81 秒，22.55 秒时播放完并返回待机。此为特定短句单次测量，不代表所有输入的延迟，也不是严格迁移前后性能对照。

## 部署信息与插件打包

私有.env必须设置LINGCAST_PUBLIC_ORIGIN（HTTP(S) origin，不带路径）。Compose由此设置S3公开链接及CORS；前端显示window.location.origin，不编译真实地址。插件以`python3 extensions/package_chrome.py`（默认读取私有.env）打包，ZIP不进Git，也不含任何密钥。采集改为后台生成每数字人随机Bearer，旧共享口令已废弃。不要把私有.env、数据库、真实采集数据、构建ZIP或历史备份bundle提交到Git。
