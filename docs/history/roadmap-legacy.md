# 历史路线图（2026-11-02归档）

仅供追溯，不是当前执行指令。以[当前状态](../TODO.md)与专项手册为准。

# Roadmap: 从离线视频生成 → 实时交互直播平台

> Role: AI Full-Stack & Streaming Architecture Agent

## Mission Overview

The "Talking Avatar Platform" currently works perfectly in a local macOS Docker
environment using a decoupled architecture (Go API + Python Worker + RustFS +
Redis).

Our next evolutionary leap is to upgrade the platform from an **"Offline
Asynchronous Video Generator"** to a **"Real-time Interactive Streaming
Platform"**, while also migrating the compute layer to a Linux + AMD GPU
environment.

Execute the following Roadmap **sequentially**. Do NOT move to Phase 2 before
Phase 1 is fully tested.

## 状态图例

- ✅ 已完成并提交
- [~] 代码已就绪，待 AMD 机器实机验证
- [ ] 未开始

---

## Phase 1: Compute Migration & AMD GPU Enablement（计算迁移）

**Goal:** Migrate the Python Worker from macOS (MPS/CoreML) to Ubuntu Linux with
AMD GPUs using ROCm.

### Tasks

1. **Worker Dockerfile Update (AMD ROCm)** — ✅
   - Official ROCm PyTorch image is now the default in `worker/Dockerfile`.
   - Base image: `rocm/pytorch:rocm7.2.4_ubuntu24.04_py3.12_pytorch_release_2.10.0`.
   - `uv sync --inexact` is used to preserve the PyTorch GPU build.
   - 接入 `docker-compose.yml`（`worker-rocm` 服务，profile: rocm，透传
     `/dev/kfd` + `/dev/dri`）。
2. **ONNX Runtime Adjustments** — ✅
   - 已实现：`onnx_utils.build_session()` 自动检测 `ROCMExecutionProvider`，
     支持 `WAV2LIP_PROVIDER=rocm`，逐节点回退 CPU。
   - 已实现：`real.py` 默认使用 `Wav2LipOnnxLipSync`（ONNX 后端）。
3. **Environment Parity Check** — [~]
   - 在 Ubuntu ROCm 容器内验证 S3/Redis 连接逻辑（`--network=host`）。
   - 跑通一个测试任务，生成时间与 macOS CoreML 基准（约 10 秒）相当或更快。

## Phase 2: Business Logic Completion（业务闭环）

**Goal:** Complete the Avatar listing feature on the Frontend and API.

### Tasks

1. **Go API (Backend)** — ✅
   - `GET /api/avatars` + `GET /api/avatars/:id`（轮询初始化状态）；创建时
     `voice_id`/`status=initializing`/`avatar_init` 队列；base 视频回写 Webhook。
2. **Frontend (shadcn-admin)** — ✅
   - Avatar Library 响应式卡片 + 状态徽标；Avatar Studio 改为纯创建页
     （名称/图片/Edge-TTS 音色 + 初始化轮询）；Broadcast 播报页；Live Studio。

## Phase 3: Streaming Architecture Reboot（流式架构）

**Goal:** 引入 RTMP 流媒体网关，将 Worker 推理管线重构为
**内存 → 网络持续推流**，不再落盘 MP4。

### Step 3.1: SRS Streaming Gateway — ✅

1. ✅ 在 `docker-compose.yml` 集成 SRS（`ossrs/srs:5`）容器。
2. ✅ 端口：仅发布 1935 RTMP 供宿主机 Worker 推流；1985 HTTP API 与
   8080 HTTP-FLV 只在内网（避免向宿主机开放调试端口）。
3. ✅ 拉流经 Nginx `/live/` 代理到 `srs:8080`（内网）。

### Step 3.2: Streaming Pipeline Refactor（交互式问答）— ✅

1. ✅ 分句处理：`POST /api/live/{id}/push` 按 `。！？!?；;`/换行切句，顺序入队
   `live_queue:{id}`。
2. ✅ 内存 → RTMP：`stream_worker.py` 常驻 ffmpeg 双管道（BGR24 帧 stdin +
   s16le 音频 /dev/fd/3，视频输入 `-re` 实时节流）推流
   `rtmp://localhost:1935/live/avatar_<id>`；闲置态喂静音音频 + base 动画，
   说话态喂口型帧 + TTS 音频，管道全程不关闭。离线 MP4 逻辑保留。
3. ✅ `POST /api/live/{id}/start|push|status` 已落地；LLM 链路由
   `POST /api/live/{id}/message` 完成（OpenAI Go SDK → DeepSeek Responses
   `deepseek-v4-flash` → 回复切句入 `live_queue:{id}`；无 key 时回显原文），
   **已异步化**：接口先 202 立即返回，LLM 生成 + 入库 + 入队在后台 goroutine
   执行（30s 超时），发送不再阻塞。观众端已拆为**独立 Next.js +
   TailwindCSS 项目**（`frontend/live/`，:3000）：`/` 开播列表 +
   `/rooms/:avatarId` 直播间（xgplayer + 服务端代理）。

### Step 3.3: 7x24 Long-form Broadcast（长时直播）— [~]

1. [~] 异步多线程：闲置期间 TTS 异步预取，说话切换不等待；base 动画按流缓存
   （`LIVEPORTRAIT_IDLE_BASE_SECONDS` 控制，默认 10s）。
2. [ ] 会话保持 / 断流重连 / 多流并发资源调度（未开始）。

---

## Phase 4: 直播体验与智能升级（口型 / 性能 / 记忆 / 知识库）

**Goal:** 从「能用」走向「好用」：修复口型变形、消除直播卡顿，并让数字人
具备长期记忆与私有知识库（RAG），减少多轮对话降智与专业问答幻觉。

### Task 4.1 口型变形修复（Lip-sync Deformation）— ✅

- ✅ 已实现双轨人脸修复引擎（`worker/ai/enhancer.py`，纯 ONNX）：
  - **直播（GFPGANEnhancer）**：GFPGANv1.4.onnx 只修复人脸 ROI（Bounding Box），
    羽化遮罩（Feathered Mask）贴回原帧，省约 80% 计算量。
  - **离线（CodeFormerEnhancer）**：codeformer.onnx 全脸修复，
    `fidelity_weight`（w=0.6，ONNX 的 `w` 输入）可调。
- ✅ 模型下载：`uv run python download_models.py --models restoration` →
  `worker/models/restoration/{gfpgan,codeformer}/*.onnx`（已实测跑通）。
- ✅ 管线接入：`lipsync_onnx` 帧级增强钩子（有 enhancer 才生效）；`real.py`
  （离线）默认 codeformer（`FACE_ENHANCER=off` 可关）；**直播管线不使用人脸
  增强**（Watchdog 需恒定 24fps），缺模型自动降级 no-op。
- ✅ 直播增强实测结论（2026-08）：本机 CoreML 下 CodeFormer ≈1.4s/帧、
  GFPGAN ≈2.5s/帧，直播开启会滞后 ~1 分钟，**维持默认关闭**；增强留待
  Linux CUDA/ROCm 机器（GPU 上 GFPGAN ROI）再评估。
- ✅ 实机画质验收：离线成品对比视频已放 `docs/videos/`
  （`noCodeFormer.mp4` vs `CodeFormer.mp4`），观感提升明显；后续可在不同
  形象/语速下微调 `roi_padding`/`feather_ratio`/`CODEFORMER_FIDELITY_WEIGHT`。

### Task 4.2 口型性能与推流卡顿（Latency / Streaming Stutter）— ✅

- ✅ **Watchdog 架构已落地**（`stream_worker.py`）：
  - **消费推流线程（Watchdog）**：独立线程以恒定 `fps`（默认 24）向 ffmpeg
    写帧，读 `Ready_Frames_Queue`；队列空时**立即回退** base 动画帧 + 静音音频，
    推流永不中断、播放器不转圈。
  - **生产推理线程**：异步 Edge-TTS → Wav2Lip 小批量（8 帧）产帧入队，
    首批即切换说话；连续多句无缝拼接（产帧快于实时）。
  - ffmpeg 去掉 `-re`，改由 Watchdog 在 Python 侧精确节流（避免 lag→EOF 断流）。
- [ ] 实机长播压测：多轮连续弹幕 + 长文本，观察推流/播放器是否持续无卡顿。

### Task 4.3 长期记忆（Long-term Memory）— ✅

- ✅ 已实现：Go `llmChat` 每次回复前取该数字人最近 10 条房间消息
  （`live_messages`，按 avatar_id 作为会话）注入 System Prompt
  （user/assistant 格式），支持多轮连续对话；窗口外旧消息直接丢弃控 Token。

### Task 4.4 私有知识库（RAG）— ✅

- ✅ Part 1 + 2 + 3（已重构）：知识库升级为**全局共享 + N:N 绑定** ——
  知识库（Collection，`knowledge_collections`，全局唯一名称、不再属于某个
  机器人）→ 文档（Document，`knowledge_documents`：text/.txt/.pdf，源文件
  入 S3，Go 用 `ledongthuc/pdf` 提取 PDF 文本）；机器人通过
  `avatar_knowledge`（avatar_id + collection_id 复合主键，enabled 开关）
  绑定若干知识库，多个数字人可共用一个；`service-rag` 微服务（zvec 全文
  索引 + Jieba 中文分词，**零模型/零下载**）负责入库 `/v1/knowledge/ingest`、
  检索 `/v1/knowledge/search`（按 avatar_id / collection_id /
  collection_ids 标量过滤 + BM25 Top-3）、删除 `/v1/knowledge/delete`、
  分块查看 `/v1/knowledge/chunks`。Go 聊天端点先查该数字人
  `enabled=true` 的绑定集合，再按 collection_ids 检索 Top-3 注入
  System Prompt；观众只发关键词时视为「想了解该主题」主动讲解。
- ✅ **检索方案演进（已落地）**：原计划「RedisStack（RediSearch）做 KNN +
  BAAI bge-small 向量化」已由 **service-rag（zvec 进程内全文索引 + 自带
  Jieba 中文分词）** 取代——不需要向量模型、不需要 RediSearch、零下载；
  Redis 已回退 `redis:8.2.2-alpine`，`worker/rag_worker.py` 及其依赖
  （pymupdf / sentence-transformers）已删除。
- ✅ **查询与拼接**：观众提问 → service-rag 按数字人聚合 Top-3 → 作为
  `<Context>` 注入 DeepSeek System Prompt，强制只根据知识库回答，减少
  带货/专业问答幻觉；管理端可在线检索测试并查看文档分块。

---

## 近期已完成（Roadmap 之外落地）

- ✅ **知识库管理后台**：入库页（`/knowledge`，文本/.txt/.pdf）+ 列表页
  （`/knowledge` 知识库列表 + `/knowledge/$id` 文档详情：创建/重命名/删除
  知识库、文档增删、按知识库在线 Top-3 检索测试）。
- ✅ **知识库全局化**：知识库从「属于单个数字人」改为「全局共享集合」，
  数字人编辑页右侧「知识库」面板勾选绑定（即时保存）；已入库旧数据自动
  迁移为绑定关系（`migrateGlobalKnowledge`），检索按绑定集合隔离。
- ✅ **聊天日志页**：`/chat-logs` 按数字人/用户 ID/日期/关键字检索 + 分页
  （`page/pageSize` + total）；机器人回复持久化 `rag_hit`/`rag_sources`，
  页面标注「命中知识库」并可展开查看命中的知识片段。
- ✅ **客户端用户中心**：`/account` 身份卡（游客/账号）、注册/登录/退出、
  「我的消息」（`GET /api/chat/history?userId=`）；导航身份胶囊可点击进入；
  首页美化（Hero CTA、卡片悬停进入直播间、页脚账号中心入口）。
- ✅ **直播链路稳定化**：音频切片先于视频帧写入（防 AAC 欠载）、ffmpeg
  保留 `-re`（Watchdog 兜底填帧）、Redis 断连 1s 静默退避。
- ✅ **RAG 迁移到 service-rag**：compose 新增 `service-rag`（zvec FTS +
  Jieba，volume `rag-zvec-data`），`EMBED_SERVER_URL` 默认
  `http://service-rag:8001`；Redis 回退 `redis:8.2.2-alpine`（不再需要
  RediSearch），`worker/rag_worker.py` 与脚本中的 rag_worker 已删除。
- ✅ **TTS 微服务（service-tts）**：async `edge_tts.Communicate` → ffmpeg
  转 16kHz/16-bit/mono PCM WAV → 上传 S3（RustFS，S3 配置走环境变量）→ 只返回
  S3 key + 元数据，`finally` 清理临时文件；compose 内网 :8002，不发布宿主端口。
- ✅ **端口收敛**：宿主机不调试，SRS 只发布 1935（RTMP 推流），1985/8081
  收回内网；`api`/`service-rag`/`service-tts` 均不发布端口；对外仅
  3000/8080/1935/6379/9000。
- ✅ **TTS 试听接口重构（后效优化）**：`POST /api/tts/preview` 音色试听已改为
  直接 HTTP 调用 `service-tts` 微服务的 `/v1/tts/preview`（试听为一次性临时
  数据，直接返回字节、不走 S3）；`backend/Dockerfile` 已移除 `python3` /
  `edge-tts` 依赖，后端镜像不再捆绑 Python。
- ✅ **存储与任务队列演进**：MinIO → RustFS（SNSD 单盘模式）；S3 环境变量
  双命名兼容（`S3_*` 主约定 + `RUSTFS_ENDPOINT_URL/AWS_ACCESS_KEY_ID/
  AWS_SECRET_ACCESS_KEY/S3_BUCKET_NAME` 别名，path-style）；worker 支持
  `{type:"render",text,tts_s3_key,base_video_s3_key}` 任务（base 视频 LRU
  缓存、TTS wav 用完即删），旧 taskId 格式向后兼容。
- ✅ **直播会话恢复修复**：api-scheduler 补 `GET /api/live`，stream_worker
  重启后按 DB 恢复直播会话。
- ✅ **前端容器加固**：frontend-admin nginx 设 Asia/Shanghai 时区；
  frontend-live 改为非 root（node）运行（chown 在 COPY 之后，`/app` 可写）。
- ✅ **场景（数字人 → 场景 → 视频）**：`scenes` + `scene_videos` 两张表；
  场景有标题/描述/封面，视频有描述；创建数字人的 base 视频成为默认场景的
  默认视频（直播/播报兜底，默认场景/默认视频不可删）。接口：
  `GET/POST /api/avatars/:id/scenes`、`PUT/DELETE /api/scenes/:id`、
  `POST /api/scenes/:id/videos`、`DELETE /api/scenes/:id/videos/:vid`；
  任务创建支持 `sceneId + videoId`；人物设定收进 `avatars.persona` JSON，
  `base_video_s3_key` 与旧 `avatar_videos` 彻底移除（启动时一次性迁移）。
- ✅ **任务进度分阶段 + TTS 复用**：worker 按 `tts/lipsync/mux` 上报 stage、
  每 1% 上报 progress（前端进度条 + 阶段标签）；TTS 首次合成后缓存到 S3
  （`tts/tasks/{id}.wav`），重试直接复用不再合成；删除任务时一并清理。
- ✅ **直播默认推流视频（场景 + 切换）**：`live_settings` 新增
  `idleSceneId/idleSwitchMode(interval|random)/idleSwitchSeconds`，Live Studio
  移除「发送文字」、新增「默认推流视频」卡片（选场景 + 定时 N 秒/随机切换）；
  start 控制消息与 `GET /api/live` 携带 `idleVideos`（所选场景全部视频 S3 Key）；
  worker 下载全部闲置视频（同分辨率），闲置态定时顺序或随机（5-30s）切换；
  **说话口型基于当前正在显示的那段场景视频**（`_slice_current_idle`），说完从
  该视频衔接处继续，不再固定默认视频。
- ✅ **Agentic 场景/动作视频（1v1）**：DeepSeek System Prompt 注入当前场景全部
  动作视频（S3 Key + 描述），LLM 可在句首输出 `<action:S3_KEY>`；后端
  `parseActionTag` 剥离标签（不入库/不显示/不说出）并把 key 作为该句
  `base_video_s3_key`（无标签回退默认视频）；`PUT /api/live/session/:id/scene`
  切换活跃场景（更新 `live_sessions.scene_id` + `live_settings.idleSceneId`，
  推 `switch_scene` 控制消息带 `video_pool`）；worker 用 `_idle_lock` 原子替换
  闲置视频池（不打断 Watchdog），动作句按需 S3 加载（LRU 缓存）；观众端
  `SceneSwitcher` 胶囊条 + 切换反馈。
- ✅ **Telegram Mini App 登录 + Ngrok 隧道**：`POST /api/auth/telegram`（api-telegram）
  校验 `initData`（HMAC-SHA-256 + 24h 时效）并 upsert `live_users/telegram_users`（按
  `telegram_id`，非游客，HttpOnly `tg_uid` cookie）；`frontend/telegram` 用
  `@twa-dev/sdk` 取 `WebApp.initData` 调该接口（`VITE_API_ORIGIN` 可指向 ngrok
  api-gateway）；根 `ngrok.yml` 双隧道 + compose `ngrok` 服务，`TG_BOT_TOKEN`/
  `NGROK_AUTHTOKEN`/`VITE_API_ORIGIN` 见 `.env.example`。
- ✅ **Telegram Webhook 自动发现与注册**：api-telegram 启动时后台 goroutine 轮询
  `NGROK_API_URL`（`http://ngrok:4040/api/tunnels`，2s × 10 重试）取
  `api-gateway` 公网地址 → `setWebhook` 注册 `/api/telegram/webhook`（占位
  200 OK）；单测覆盖发现/未就绪/注册/重试四类场景。
- ✅ **TG 注册环境变量优先**：`TG_WEBHOOK_URL`/`TG_MINIAPP_URL` 优先于 ngrok
  （生产固定域名）；缺失时按隧道发现（api-gateway→webhook、tg-app→菜单按钮），
  两者都有则跳过 ngrok；`setChatMenuButton` 注册 Mini App 入口，来源日志清晰。

## Strict Rules for Execution

1. 先给出 Phase 1 所需的精确命令与代码调整，测试通过后再进入下一阶段。
2. 不重写整个系统；模块化注入改动，保持现有 API + DB + S3 + Nginx 架构不变。
3. Phase 3 优先稳定性而非极致低延迟：先保证分句与 FFmpeg 管道不崩溃，再优化线程。
