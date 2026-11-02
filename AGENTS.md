# LingCast 开发交接

2026-11-02按代码核对。文档不是线上状态证明；执行变更前检查工作区、实际私有配置和服务状态。

## 当前架构

- 管理端：React 19 / TypeScript / Vite 8 / Tailwind CSS 4 / shadcn/ui / TanStack Router与Query，pnpm 10.25。
- 观众端：独立Next.js 16 / React 19，xgplayer HTTP-FLV；另有web与Telegram客户端。
- 后端：Go 1.25、Gin、GORM、AWS S3 SDK v2，多入口admin/live/telegram/web/scheduler。
- 基础设施：MariaDB 11、Redis 8.2、RustFS、SRS v5、Nginx；RAG为FastAPI + zvec/Jieba。
- 生产直播：Qwen Chat Completions SSE完整短句 → CosyVoice3 PCM块 → MuseTalk 1.5逐帧 → 常驻FFmpeg/SRS。手动口播原文直说，不经LLM。
- 宿主机Worker由uv管理，项目Python约束>=3.11,<3.14，`.python-version`为3.12；模型各有独立环境。当前GPU链路使用Linux NVIDIA CUDA，其他平台的历史支持不等于该链路已验收。

## 核心实现入口

| 范围 | 源码 |
| --- | --- |
| 当前依赖与环境 | `backend/go.mod`、各前端`package.json`、`worker/pyproject.toml`、`.python-version` |
| LLM与过滤 | `backend/internal/handlers/live/llm_stream.go`、`live_guard.go`、`backend/internal/config/config.go` |
| 主持与固定稿 | `backend/internal/handlers/live/host*.go`、`programme.go`、`worker/ai/host_director.py` |
| 原文口播与项目读取 | `backend/internal/handlers/live/project.go`、`frontend/admin/src/features/live-studio/project-reader.tsx` |
| Chrome采集 | `extensions/chrome/`、`backend/internal/handlers/live/collector.go`与`xhs.go` |
| 队列与播放 | `worker/streaming/incoming.py`、`stream_worker.py`、`streaming/ffmpeg_pipe.py` |
| 常驻模型与缓存 | `worker/cosyvoice_service.py`、`musetalk_service.py`、`ai/musetalk_client.py`、`ai/musetalk_runtime.py` |
| 换装 | `backend/internal/handlers/live/wardrobe.go`、`host_wardrobe.go`、`worker/ai/wardrobe.py`、`ai/prepared_looks.py` |
| 界面 | `frontend/admin/src/features/live-studio/` |

## 必须保持的行为

- 当前短段讲完再取下一项：手动口播 → 礼物感谢 → 点赞感谢 → 评论回复/点播 → 欢迎 → 主持。不承诺2秒开口，2秒是欢迎/回复触发限频。
- 主持普通固定稿24条，ID3启用换装时追加3条提醒；首条普通稿准备好即可播放，其余后台补齐。自定义主持1～12段、每段5～70字。
- ID3换装严格按旧衣告别 → 静音离场/空镜/入场 → writer提交新idle和renderer且显示idle → 回场语 → 释放队列。口型profile、输入SHA和底片Key必须对应。
- 当前新酒红/香槟使用新增loopback副本，白衣仍走原服务；实际地址和清单以私有Worker配置为准，不按历史服务单元盲目覆盖。Worker重建有短暂重连，不宣称无缝热更。
- 完整PCM/JPEG缓存按模型、参考声音、文字和形象隔离，最多256项；磁盘缓存与主持内存准备状态不同，不完整流不缓存，指纹变化或淘汰会重新生成。
- 只使用用户手动启动的Chrome DOM采集，不重新启用旧浏览器自动化采集，不操作平台评论输入框。页面实际未展示的信息不能猜测。
- 当前采集凭据为每数字人32字节随机Bearer，Redis仅存哈希且TTL为0，响应`expires_ms=0`。重新生成/撤销使旧密钥失效，不写成12小时自动过期。
- 已放弃按昵称维护第三方观众连续会话和唱歌需求，不因旧实验目录存在就恢复这些功能。

## 变更、验证与部署

1. 按请求在本地修改，只提交有关文件；保留他人/既有未跟踪实验与私有素材。
2. 按改动运行适当检查；文档使用`cd docs && pnpm build`，后端`cd backend && go test ./...`，插件`node --test extensions/chrome/tests/*.test.mjs`。不把未跑的GPU联调写成通过。
3. 提交推送、远端快进同一版本后，按范围更新：文档仅同步；插件仅打包/加载；业务只更新相关服务。模型重启需明确授权，不为普通更新执行全量`deploy/chengdu.sh`。
4. 不擅自开外部RTMP或发测试评论；保留当前直播、采集、主持与密钥配置。回退先恢复绑定的一整套清单/服务地址，不只替换idle。

生产仅公开必要后台网关，其余内网或loopback。地址/模型参数从环境读取，不提交真实部署地址、认证值、推流密钥、运行日志、观众记录、模型或运行素材。插件默认服务器从私有`.env`打包注入且可编辑，不硬编码真实地址。运行时媒体不提交，但已审核用于文档的静态截图可在`docs/images/current/`提交，并记录来源。

## 专项手册

- [README](README-zh.md)与[架构](docs/技术需求与架构文档.md)
- [流式链路](docs/cosyvoice-musetalk-live.md)、[部署](docs/chengdu-deployment.md)
- [自主主持](docs/autonomous-host.md)、[换装](docs/wardrobe.md)、[项目速读](docs/project-reader.md)
- [Chrome采集](docs/chrome-collector.md)、[感谢](docs/thanks-priority.md)、[点播](docs/viewer-interaction.md)、[防注入](docs/live-prompt-guard.md)
- [历史引擎](docs/history/legacy-readme-zh.md)和[历史路线](docs/history/roadmap-legacy.md)仅供追溯，不作为当前执行指令。
