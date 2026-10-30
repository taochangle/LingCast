# Talking Avatar Platform — 开发交接说明

本文档供 AI Agent 或开发者在**新设备上 clone 本仓库后**快速理解现状并继续开发。
完整的上手步骤见 [README.md](./README.md)。

## 1. 项目是什么

端到端口播数字人平台：管理后台上传形象图片 + 选择 Edge-TTS 音色创建数字人，后台用
**LivePortrait 预处理生成基础视频 + Edge-TTS 语音 + Wav2Lip(ONNX) 口型合成**生成
带声音的说话视频。

## 2. 当前技术栈（已落地，勿按旧文档改回）

- 前端：React + TypeScript + Vite + Tailwind + shadcn/ui（基于 satnaing/shadcn-admin），
  pnpm 10.25.0 管理，`frontend/admin/`（BFF 多客户端矩阵：`admin/live/web/telegram`）。
- 后端：Go + Gin + GORM，`backend/`（模型、S3、Redis、handlers 分为 admin/live/telegram/web）。
- AI Worker：Python **3.11**（`worker/.python-version`），uv 管理依赖（`pyproject.toml`
  + `uv.lock`），**不要使用 requirements.txt**。
- 存储：S3 兼容对象存储（RustFS，Docker Compose 内运行）；Go 用 `aws-sdk-go-v2`，
  Python 用 boto3；服务间只传 S3 Key，不用本地路径。
- 基础设施：Docker Compose；**MariaDB 11** + **Redis 8.2.2-alpine** + RustFS
  + **service-rag**（本地 RAG 微服务：zvec 全文索引 + Jieba 中文分词，
  Docker 内 `services/rag/`，端口 8001，零模型依赖）+ **service-tts**
  （Edge-TTS 微服务：async edge-tts → 16kHz PCM WAV → S3，`services/tts/`，
  端口 8002，S3 共享存储，媒体不过 HTTP）+ **docs**（API 文档网关：nginx
  聚合各 HTTP 微服务的 Swagger/OpenAPI，经管理端 `:8080/doc/<prefix>` 访问）。
- 部署模式：Docker 里跑基础设施 + API + 前端 + 轻量 Mock Worker；**真实 AI Worker
  在宿主机原生运行**：macOS Apple Silicon（MPS/CoreML）、Linux NVIDIA CUDA、
  Linux **AMD ROCm**（RX 6800 XT 等 RDNA2，见 README 对应章节）。宿主机仅开放
  必要端口：3000（观众端）/ 8080（管理端）/ 1935（RTMP 推流）/ 6379（Redis）/
  9000（RustFS）；`api-admin`/`api-live`/`api-telegram`/`api-web`/`api-scheduler`、`service-rag`、
  `service-tts` 均在内网，不发布端口。
- 依赖组（`worker/pyproject.toml`）：`models`（macOS 默认）、`cuda`、
  `rocm`（仅 `onnxruntime-rocm`，torch 用官方 ROCm 镜像或手动装）——cuda/rocm
  互斥，Linux 用 `--no-group` 二选一；ROCm 容器里 `uv sync` 必须加 `--inexact`
  保留镜像预装的 torch。

## 3. 当前实现状态

- ✅ Avatar Studio（创建）：形象名称/头像展示/图片/Edge-TTS 音色（localStorage 缓存，
  默认 zh-CN-XiaoxiaoNeural）+ 人物设定（年龄/身高/体重/族裔/感情状态/性格）。
  **创建数字人不再自动生成视频**：只建身份 + 默认场景，`ready` = 至少一个场景 +
  至少一条视频（上传或生成均可），视频全部由用户在「场景」页添加。
- ✅ Broadcast（离线播报）页面：选数字人 + 脚本 → 任务轮询 → 播放成品。
- ✅ 场景（Scene）两级结构：一个数字人可有多个场景（标题/描述/封面），每个
  场景下 1-N 个驱动视频（视频 + 描述）；创建数字人时的 base 视频成为默认
  场景的默认视频（直播/播报兜底，不可删）。表：`scenes` + `scene_videos`
  （均带 avatar_id 索引，支持按数字人筛选）。人物设定（年龄/身高/体重/族裔/
  感情/性格）收进 `avatars.persona` JSON；`base_video_s3_key` 与旧
  `avatar_videos` 已彻底移除（启动时一次性迁移）。
- ✅ 任务进度 + TTS 复用：离线任务上报 `stage`（tts/lipsync/mux）+ `progress`
  （1% 步进），前端任务中心/播报历史显示阶段与进度条；TTS 首次合成后存
  `tts/tasks/{id}.wav`（S3），重试时直接复用、跳过 Edge-TTS。
- ✅ 客户端直播间（观众端）：**独立 Next.js + TailwindCSS 项目**（`frontend/live/`，端口
  **3000**，不属于管理后台）——首页有导航头（身份/注册/登录/退出）与**分类筛选**
  （`GET /api/live` 携带 avatar 的 `category`），`app/rooms/[avatarId]/page.tsx`
  用 xgplayer 拉 HTTP-FLV + 聊天输入 `POST /api/live/:id/message`；`app/api/[...path]`
  与 `app/live/[...path]` 路由处理器在**服务端**代理（`API_ORIGIN` / `LIVE_ORIGIN`
  运行时读取，docker 内为 `http://api-live:8082` / `http://srs:8080`，本地默认
  `http://localhost:8080`）。
- ✅ 聊天身份与记录持久化：`live_users/telegram_users`（游客/账号，bcrypt）与 `live_messages`
  （user/bot）两张表；`POST /api/chat/guest` 发临时身份，`register` 原地升级游客行
  （同一 userId 保住历史），`login` 把当前游客的消息合并进账号，退出后重新取新游客
  ID；`GET /api/chat/history?avatarId=` 拉持久化聊天记录；客户端身份存
  localStorage（`tav_chat_identity`），由 `IdentityProvider` 全局管理，注册/登录弹窗
  首页与直播间共用。`POST /api/live/:id/message` 会同时入库用户消息与机器人完整回复。
- ✅ 直播配置：Avatar 表 JSON 字段 `live_settings`（字幕
  `subtitleEnabled/subtitleFont/subtitlePosition/subtitleBorder/subtitleSize` +
  闲置推流 `idleSceneId/idleSwitchMode(interval|random)/idleSwitchSeconds`），
  `PUT /api/avatars/:id/live-settings` 读写；start 控制消息与 `GET /api/live`
  都会带上配置（含 `idleVideos` S3 列表）；worker 的 `SubtitleRenderer` 支持
  顶部/底部、描边宽度、字号，字体按文件名从 `worker/fonts/` 解析（缺失回退系统
  默认）；Live Studio「字幕设置」与「默认推流视频」卡片保存后自动重启直播生效。
- ✅ 管理端用户列表：`GET /api/users`（游客+账号，含消息数），侧边栏「用户相关 →
  用户列表」（/users）展示真实数据。
- ✅ **Telegram Mini App 登录**：`POST /api/auth/telegram`（api-telegram）用
  `TG_BOT_TOKEN` 校验 `initData`（HMAC-SHA-256：`secret=HMAC("WebAppData", token)`，
  对除 `hash` 外按字典序的 `key=value` 串签名，24h 新鲜度检查），通过后按
  `telegram_id` upsert `live_users/telegram_users`（非游客，用户名优先 TG username、冲突回退
  `tg_<id>`），设置 HttpOnly `tg_uid` cookie 并返回 `{userId,username,...}`；
  `frontend/admin/nginx.conf` 单独把 `/api/auth/telegram` 路由到 api-telegram（TMA
  走同一网关）。前端 `frontend/telegram` 初始化 `WebApp.ready()` 后把
  `WebApp.initData` POST 到 `${VITE_API_ORIGIN}/api/auth/telegram`，含加载中/
  成功/失败三态。
- ✅ **Ngrok 本地联调**：根目录 `ngrok.yml` 双隧道（`tg-app`→宿主机 3002、
  `api-gateway`→宿主机 8080）；compose 新增 `ngrok` 服务
  （`ngrok/ngrok:latest`，`start --all`，4040 检查台，`host.docker.internal`
  已映射 host-gateway）；`.env(.example)` 需填 `NGROK_AUTHTOKEN`/`TG_BOT_TOKEN`/
  `VITE_API_ORIGIN`（TMA 构建期注入 api-gateway 的 https 地址）。
- ✅ **Telegram Webhook 自动注册**：api-telegram 启动后在后台 goroutine 轮询
  `http://ngrok:4040/api/tunnels`（`NGROK_API_URL`，2s × 10 次重试），找到
  `api-gateway` 隧道的 `public_url` 后 POST
  `https://api.telegram.org/bot{token}/setWebhook` 注册
  `{public_url}/api/telegram/webhook`（不阻塞 Gin 启动）；占位端点
  `POST /api/telegram/webhook` 返回 200，管理端 nginx 将其与
  `/api/auth/telegram` 一起路由到 api-telegram。
- ✅ **Telegram 注册环境变量优先**：`TG_WEBHOOK_URL` / `TG_MINIAPP_URL` 设置后
  直接使用（生产固定域名），缺失时才轮询 ngrok 对应隧道（webhook→api-gateway，
  菜单按钮→tg-app）；两者都设置时完全不查询 ngrok；`setWebhook` +
  `setChatMenuButton`（web_app）分别注册，日志明确标注 URL 来源
  （Environment / ngrok）。
- ✅ 国际化（中/英）：管理端（react-i18next，`frontend/admin/src/i18n/`）与观众端
  （`frontend/live/lib/i18n.tsx` 轻量 provider）均有语言切换（localStorage 记忆 +
  浏览器语言自动检测）；后端按 `Accept-Language` 本地化错误消息
  （`backend/internal/i18n`，router 注入中间件）；LLM 人设提示词按请求语言生成
  （`chatSystemPrompt(avatar, lang)`，含 `en` 单测）。
- ✅ Live Studio 悬浮监看：页内播放器已移除，改为右下角圆形 FAB，点击弹出可拖动的
  9:16 浮窗（默认不渲染，按需开启）。
- ✅ LLM 回复链路：Go 后端用 `github.com/openai/openai-go` Responses API 调 DeepSeek
  （`OPENAI_BASE_URL=https://api.deepseek.com`、`OPENAI_MODEL=deepseek-v4-flash`，
  该模型是 DeepSeek 唯一支持 Responses 的模型），回复 `splitSentences` 入
  `live_queue:{id}` + `live_history:{id}`；`chatSystemPrompt()` 会把 Avatar 的人物
  设定拼进 system prompt（有单测 `live_test.go`）；无 `OPENAI_API_KEY` 时原样回读输入。
- ✅ Go API：`POST /api/avatars`、`GET /api/avatars`、`POST /api/tasks`、
  `GET /api/tasks/:id`、`POST /api/tasks/:id/status`（Worker 回调）。
- ✅ 管理员登录：`POST /api/admin/login` 校验 `ADMIN_USERNAME`/`ADMIN_PASSWORD`
  （默认 admin/admin123，务必修改），Redis 存 24h 会话 + HttpOnly cookie
  `admin_token`；`GET /api/admin/me` 校验、`POST /api/admin/logout` 退出。前端
  `/login` 登录页 + `_authenticated` 路由 `beforeLoad` 守卫；管理端写操作与
  `/api/users` 走 `RequireAdmin()` 中间件，观众端接口与 Worker Webhook 保持公开。
  账号首次启动时按 env 播种到 `admin_users` 表，之后可通过
  `POST /api/admin/change-name|change-password` 修改并持久化（登录返回
  username + displayName，侧边栏显示 displayName）。
- ✅ 视频生成独立化：`avatar_init` 为遗留流程（兼容旧任务）；新流程走
  **video-gen 微服务**（`services/video-gen`，FastAPI，端口 8003）——Go 在
  `scene_videos` 建 `generating` 记录并提交任务，宿主机 Worker 弹队列按
  provider 渲染（`worker/ai/video_providers.py` 注册表：liveportrait 可用、
  comfyui 预留），产物上 S3 后回调 `POST /api/scene-videos/:id/complete`。
  生成参数挂在**场景视频**（`scene_videos.generation_settings`），不再属于数字人。
  场景「添加视频」弹窗三 Tab：本地上传（文件+描述，必须描述）/ LivePortrait
  （描述+源图+全部参数）/ 其他（预留）。使用时仍为 **Edge-TTS** → Wav2Lip ONNX →
  mux/推流。
- ✅ 直播管线 `stream_worker.py`（闲置/说话循环）：`POST /api/live/{id}/start`
  通知 Worker 打开**常驻 FFmpeg 管道**（闲置态喂所选场景的全部驱动视频 +
  numpy 静音音频，定时 N 秒顺序切换或随机切换；未配置时回退默认视频单循环）；
  `POST /api/live/{id}/push` 按句切块入 `live_queue:{id}` → 异步 TTS → Wav2Lip
  内存出帧（口型基于**当前正在显示的那段场景视频**切片，说完从该视频衔接处
  继续）→ 口型帧 + TTS 音频替换推流，句子结束自动回闲置，管道不关闭。
  `GET /api/live/{id}/status` 供前端轮询队列。SRS v5 已入 docker-compose
  （仅发布 1935 RTMP 供宿主机 Worker 推流；1985 API / 8080 HTTP-FLV 只在内网，
  Nginx `/live/` 代理到 srs:8080）。
- ✅ **Agentic 场景/动作视频**：`POST /api/live/chat` 与 `POST /api/live/:id/message`
  在调 DeepSeek 前把当前活跃场景的全部视频（S3 Key + 描述）注入 System Prompt，
  LLM 可在句首输出 `<action:S3_KEY>` 指定该句动作视频；后端 `parseActionTag` 用
  正则 `<action:(.*?)>` 剥离标签（不入库、不显示、不说出），并作为该句
  `base_video_s3_key` 进入 render/live 队列（无标签回退默认视频）。观众端
  `PUT /api/live/session/:id/scene`（body `{"scene_id":N}`）切换活跃场景：更新
  `live_sessions.scene_id` + avatar `live_settings.idleSceneId`，并向
  `live_control` 推 `{"type":"control","action":"switch_scene","video_pool":[...]}`；
  worker 控制监听线程收到后**线程安全**（`_idle_lock` + 原子替换）下载并替换闲置
  视频池，不打断 Watchdog 写帧；说话句的动作视频按需从 S3 加载（LRU 缓存 4 个）。
  观众端直播间新增 `SceneSwitcher` 胶囊条（`frontend/live/components/scene-switcher.tsx`）
  覆盖在 9:16 画面上，点击切换并带加载/提示反馈。
- ✅ `worker/download_models.py --models all`：克隆外部代码、下载权重、导出
  wav2lip ONNX、创建软链接（一键可复现）。
- ✅ 性能：16 秒视频口型阶段约 10 秒；CPU 线程数受限（`WAV2LIP_THREADS`，默认 4）。
- ✅ 动画参数业务化：LivePortrait 全部可调参数（驱动速度/幅度/模式、动画区域、
  人脸裁剪、输出 fps/crf/分辨率/时长/模板）存 `scene_videos.generation_settings`
  JSON，在场景「LivePortrait」Tab 配置（`components/liveportrait-settings.tsx`，
  按 Worker 上报设备自动推荐默认模板），随 video-gen 任务负载下发，
  **Worker 不再读 env**（仅仓库路径/设备等机器级配置仍走 env）；旧
  `avatars.liveportrait_settings` 启动时迁移到默认场景默认视频后删列。
- ✅ 基础视频默认去眨眼：`renderer_real.py` 冻结眼部表情通道（`EYE_EXP_DIMS`
  取首帧值，`c_eyes/c_d_eyes_lst` 全帧复制首帧），保留耸肩/身体微晃（仅当
  `drivingSpeed != 1.0` 时生效；面板里改参数后需重试重新生成基础视频）。
- ✅ 播报成品预览：`broadcast/index.tsx` 状态卡片用 `VideoPlayerDialog`
  （xgplayer 封装），9:16 竖版最大 720×1080，模态窗播放。
- ✅ 人脸修复（Wav2Lip 口型变形）：`worker/ai/enhancer.py` 双轨 ONNX 增强器——
  离线 CodeFormer（`w` 输入保真度，默认 0.6）、直播 GFPGANv1.4（人脸 ROI + 羽化
  遮罩）；接入 `lipsync_onnx._run_batch_frames`（offline `real.py` 默认 codeformer）；
  模型放 `worker/models/restoration/`，
  `uv run python download_models.py --models restoration` 下载；缺模型自动降级为
  no-op 不中断管线。离线效果已验收，对比视频在 `docs/videos/`
  （`noCodeFormer.mp4` vs `CodeFormer.mp4`）。
- ⚠️ **直播增强实测（2026-08 记录，勿再忘）**：直播管线已接线但**默认关闭**
  （`stream_worker.py setup()` 里 `create_enhancer(pipeline="live")`，由
  `FACE_ENHANCER` / `ENHANCER_MAX_FPS` 环境变量控制）。实测本机（Apple
  Silicon MPS/CoreML）：CodeFormer ≈ **1.4s/帧**、GFPGAN ≈ **2.5s/帧**
  （GFPGAN ONNX 在 CoreML 分区效率差，反而更慢），一句话要等 ~1 分钟才开口，
  **直播不可用**——Watchdog 保证不转圈但说话严重滞后。增强只适合离线管线，
  或等 Linux CUDA/ROCm 机器再试（GPU 上 GFPGAN ROI 才划算）。
- ✅ 直播 **Watchdog 架构**（`stream_worker.py`）：独立写帧线程以恒定
  `fps`（默认 24）向 ffmpeg 写帧，读 `Ready_Frames_Queue`；队列空时**立即回退**
  base 动画帧 + 静音音频，玩家永不转圈。推理线程异步 Edge-TTS → Wav2Lip 小批量
  （8 帧）产帧入队，首批即切换说话，连续多句无缝拼接。ffmpeg 视频输入保留
  `-re`（Watchdog 兜底填帧，避免旧版 lag→EOF 复现）。管道断裂
  （`FFmpegPipeClosedError`）会让 session 标记 dead 并停止喂帧，控制监听器
  自动清理后可重新开播；Redis 断连时 1s 静默退避。
- ✅ 私有知识库（全局 Collection + 机器人 N:N 绑定 → 文档 Document，RAG）：
  - 存储：`knowledge_collections`（全局命名知识库，不再属于单个机器人）+
    `avatar_knowledge`（avatar_id + collection_id 复合主键，enabled 开关，
    多数字人可共用一个知识库）+
    `knowledge_documents`（collection_id + content + status + source_key）；
    源文件入 S3（text 生成 .txt，支持上传 .txt/.pdf，Go 用
    `github.com/ledongthuc/pdf` 提取 PDF 文本）。旧按机器人建库的数据由
    `migrateGlobalKnowledge` 自动迁成绑定关系。
  - 微服务：`services/rag/`（FastAPI + uv + zvec 全文索引，Jieba 中文分词，
    **零模型/零下载**）。Go 入库时同步 POST `/v1/knowledge/ingest`
    （`avatar_id/collection_id/source_id/text_content`），检索 POST
    `/v1/knowledge/search`（按 `avatar_id` / `collection_id` /
    `collection_ids` 标量过滤 + BM25 Top-3），删除走
    `/v1/knowledge/delete`；数据持久化在 volume
    `rag-zvec-data:/app/zvec_data`。
  - 管理后台：`/knowledge` 知识库列表（创建/重命名/删除，删除级联清文档与索引），
    `/knowledge/$id` 知识库详情（文档增删 + 检索测试）；Avatar Studio 编辑模式
    右侧「知识库」面板勾选绑定（即时保存）；聊天日志页显示「命中知识库」。
  - Go 聊天端点：`llmChat` 取最近 10 条房间消息（长期记忆）+ 按 `avatar_id`
    查 `avatar_knowledge` 里 `enabled=true` 的集合，按 `collection_ids` 检索
    Top-3 注入 System Prompt（严格按知识库回答）。
  - Go 侧 `EMBED_SERVER_URL` 默认 `http://service-rag:8001`（compose 内网）。
  - ⚠️ 旧 `worker/rag_worker.py`（bge 向量 + RediSearch）已删除，相关依赖
    （pymupdf / sentence-transformers）已从 `worker/pyproject.toml` 移除；
    `redis/redis-stack-server` 已回退为 `redis:8.2.2-alpine`。
- ✅ TTS 试听走微服务：`POST /api/tts/preview` 由 api-admin 代理到
  `service-tts` 的 `/v1/tts/preview`（一次性试听，直接返回音频字节、不走
  S3）；`backend/Dockerfile` 不再捆绑 python3 / edge-tts，正式合成仍走
  `/v1/tts/synthesize` → S3 共享存储。
- ⬜ Mock 管线（`AI_MODE=mock`，Docker Worker 镜像默认）仅为占位/轻量演示。
- ✅ 客户端用户中心：`frontend/live/app/account/page.tsx`（`/account`）身份卡 +
  注册/登录/退出 + 「我的消息」（`GET /api/chat/history?userId=`）；导航身份
  胶囊点击进入；首页 Hero CTA / 卡片悬停 / 页脚入口。
- ⬜ 待办（详见 [docs/TODO.md](docs/TODO.md)）：Phase 1 的 AMD ROCm 容器
  Dockerfile 与实机验证、Phase 4.2 实机长播压测。
- ⬜ Linux/CUDA 生产部署未实测（代码路径已预留）。

## 4. 目录速览

```text
backend/   Go API（Dockerfile）
services/
  rag/          本地 RAG 知识库微服务（FastAPI + uv + zvec FTS/Jieba，端口 8001）
  tts/          Edge-TTS 微服务（FastAPI + uv + edge-tts + ffmpeg，端口 8002，内网）
docs/         API 文档网关 + VitePress 文档站（nginx 聚合各服务 Swagger；
              宿主经 /doc/<prefix> 访问；站点构建后部署 GitHub Pages）
frontend/        BFF 多客户端矩阵
  admin/         React 管理后台（Dockerfile + nginx.conf）
  src/features/knowledge/      知识库管理（index=Collection 列表 / detail=文档）
  live/          Next.js 观众端（独立项目）：app/page.tsx 列表 + app/rooms/[avatarId] 直播间
  lib/identity.tsx      全局聊天身份（游客/注册/登录/退出）
  components/auth-modal.tsx  注册/登录弹窗
  web/           1v1 网站脚手架（Next.js + PayPal + zustand，:3001）
  telegram/      TG Mini App 脚手架（React + Vite + @twa-dev/sdk，:3002）
worker/
  worker.py              Worker 入口（Redis 队列、S3、Webhook 回调）
  download_models.py     一键克隆代码 + 下载/导出模型
  ai/
    real.py              RealPipeline：TTS → 渲染 → 口型
    lipsync_onnx.py      另有 iter_frames()/audio_pcm16() 供流式内存推理
    tts_real.py          GPT-SoVITS（本地 API server 模式）
    renderer_real.py     LivePortrait 渲染（含模板放慢/幅度调节）
    lipsync_onnx.py      Wav2Lip ONNX 口型（CoreML/CPU）
    face_detect_onnx.py  SCRFD ONNX 人脸检测
    onnx_utils.py        ONNX session 构建（线程限制、provider 选择）
    lipsync_real.py      torch 版 Wav2Lip（慢，仅 WAV2LIP_BACKEND=torch 对照用）
  external/  gitignore，外部推理仓库克隆（GPT-SoVITS/LivePortrait/Wav2Lip）
  models/    gitignore，权重（见下方“模型目录”）
  streaming/ffmpeg_pipe.py   ffmpeg 子进程管道（双输入，音频走 /dev/fd/3）
  streaming/subtitle.py      Pillow 字幕渲染（位置/边框/字号可配）
  fonts/                     gitignore：用户下载的免费字体（见 fonts/README.md）
  stream_worker.py       流式 Worker 入口（闲置/说话循环，与 worker.py 并存）
```

## 5. 关键约定

- 所有 Python 命令一律 `cd worker && uv run python ...`；新增依赖用 `uv add`，
  不写 requirements.txt。
- S3 环境变量双命名兼容：`S3_ENDPOINT/S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET`
  为项目主约定，同时接受 `RUSTFS_ENDPOINT_URL/AWS_ACCESS_KEY_ID/
  AWS_SECRET_ACCESS_KEY/S3_BUCKET_NAME` 别名（path-style，RustFS/MinIO 通用）。
- 任务队列双格式：`{type:"render",text,tts_s3_key,base_video_s3_key}`（S3
  共享存储）与旧 `{taskId,imageS3Key,scriptText,...}` 并存；render 任务按
  `type` 或按键识别分发，TTS wav 下载后由 `finally` 清理，base 视频走 LRU 缓存。
- S3 是唯一的跨服务文件通道：上传/下载都走 S3 Key/URL，不在服务间传本地路径。
- AI 逻辑与 S3/Redis 编排解耦：新模型实现 `InferencePipeline`，在
  `worker/ai/factory.py` 注册，通过 `AI_MODE` 切换。
- 权重/外部代码永不入库（`worker/models/`、`worker/external/` 已 gitignore）。
- 直播字幕字体放 `worker/fonts/`（gitignore，仅 README 入库）；设置里存的是文件名，
  换新设备需连同字体一起拷贝。
- 直播相关配置（如字幕）持久化为 Avatar 的 JSON 字段 `live_settings`，worker 通过
  start 控制消息获取；改配置后需重新 start 直播（Live Studio 保存时会自动重启）。
- 提交信息沿用仓库现有风格：`feat:` / `fix:` / `docs:` / `refactor:` 前缀 + 中文或
  英文描述。
- 修改涉及外部仓库（GPT-SoVITS/LivePortrait/Wav2Lip）时优先在 `worker/ai/` 里做
  适配层，避免直接改外部代码（升级可替换）。

## 6. 新设备快速启动（摘要）

```bash
git clone https://github.com/taochangle/LingCast.git && cd LingCast
cp .env.example .env
docker compose up --build          # 基础设施 + API + 前端

cd worker
uv sync --all-groups               # 安装全部依赖（较久）
cp .env.local.example .env.local
uv run python download_models.py --models all   # 或从旧设备拷贝 models/external
uv run python -u worker.py          # 真实管线，AI_MODE=real
```

模型快速迁移（免重新下载 ~4GB）：把旧设备的 `worker/models/` 和
`worker/external/` 整个目录拷到新设备对应位置即可，脚本会跳过已存在文件。

## 7. 已知坑（务必先读）

- **版本化发布与自然语音切段（更新）**：本地测试 → git commit/push → 成都 git pull --ff-only → bash deploy/chengdu.sh；根 Compose + deploy/chengdu.override.yml 是当前部署入口，不再用迁移时的私有 compose.json。镜像标签与 runtime/deployed-revision 记录 SHA。直接口播完整任务一次 TTS，再由 ai/speech_segments.py 按停顿切音频、逐片生成即播；不再固定 50 字切稿。细节见 docs/chengdu-deployment.md。

- **LatentSync 提速（2026-10-28，更新）**：成都常驻模型默认已设 `LATENTSYNC_STEPS=10`，源码仍有 20 步兜底；同一 5.06 秒音频、两轮中位数：20/15/10/8 步分别 24.58/20.98/17.08/15.73 秒。仅模型请求阶段，不能宣称包含浏览器延迟。样例画质接近，10 步折中；对比视频与方法见 `docs/latentsync-steps-benchmark.md`。后文旧的“20 步保留”描述已被此项替代。

- **GitHub 项目速读（2026-10-28）**：Live Studio 新增公开仓库读取、可编辑短讲稿、文件依据与按原文讲解。管理员 API `/api/projects/read` 固定默认分支提交，读取最多 7 个文件，不执行仓库代码；`/api/live/:avatarID/narrate` 限 220 字，最多 5 个 50 字短段原子入队，避免模型服务 20 秒音频上限。计划口播无聊天过期时间，每段生成期间仍待机；尚非整场连续预生成，也没有演示画面。详见 `docs/resident-live.md`。

- **常驻互动链路（2026-10-28）**：用户明确选用 IndexTTS + LatentSync。实现与恢复命令见 `docs/resident-live.md`；新云端服务在 `/data1/taoxin/lingcast-resident`、loopback 59411/59412，不替换旧 17864 服务。`worker/.env.local` 配置 `LIPSYNC_ENGINE=latentsync`，整个口播 MP4 预加载后插播，播完回到待机；OBS 入口 `/obs.html?avatar=1`。20 步参数保留，模型/人脸对齐缓存常驻。当前实测回复仍需约 26 秒，不得宣称秒级实时或已完成小红书接入。

- **Qwen 接入（2026-10-28）**：支持 `OPENAI_API=chat_completions` 与 `V2_QWEN_API_KEY/BASE_URL/MODEL`，禁用思考、拦截截断回复；原 Responses 模式保留。配置与数字人导入/同音频口型对比见 `docs/qwen-and-lipsync-comparison.md`。本机测试管理端使用 59480，勿与其他项目占用的 8080 混淆。IndexTTS/LatentSync 目前为对比测试复用，尚未接上正式直播 Worker。

- **模型目录不入库**：clone 后没有 `models/`/`external/` 是正常的，跑
  `download_models.py` 或拷贝。
- **GitHub 慢**：本机可走代理，例如
  `git -c http.proxy=http://127.0.0.1:7897 clone ...`；HF 慢可用
  `HF_ENDPOINT=https://hf-mirror.com`。
- **NLTK 版本**：必须 <3.10（已 pin）；仅在 `TTS_ENGINE=gpt-sovits` 时才下载数据
  （默认 Edge-TTS 不需要）。
- **onnxruntime**：macOS 用官方 wheel（`onnxruntime>=1.17`），不要装
  `onnxruntime-silicon`（1.16.3 的命名空间包不完整）。
- **onnxruntime-rocm 与 onnxruntime 互斥**：Linux x86_64 装 rocm 组时需
  `--no-group cuda`；反之亦然，二者都提供 `onnxruntime` 包。
- **口型别用 torch 后端**：`WAV2LIP_BACKEND` 默认 onnx；torch 版在 Apple Silicon
  上要 8 分钟且打满 CPU，仅作对照。
- **直播别开人脸增强**：本机实测 CodeFormer ~1.4s/帧、GFPGAN ~2.5s/帧，开了
  说话滞后约 1 分钟；`stream_worker` 默认关闭（`FACE_ENHANCER` 勿设）。
- **ROCm 上口型走 CPU 兜底**：`WAV2LIP_PROVIDER=rocm` 时若 LSTM 算子不在 ROCm EP
  支持集内会自动逐节点回退 CPU，不影响结果。
- **Edge-TTS 需要外网**：语音合成走微软在线服务（默认引擎）；离线环境不可用。
- **G2PW 中文前端**（仅 gpt-sovits 引擎）：首次使用会从 ModelScope 下载约 1.2GB。
- **ffmpeg**：宿主机需 `brew install ffmpeg`（torchcodec 依赖）。
- **任务卡在 processing**：先看 worker 日志尾部；口型阶段卡住多半是用了旧 torch
  后端或模型缺失（`download_models.py --models wav2lip` 可补齐）。
- **旧库启动报 Multiple primary key**：早期版本（dc867b7 前后）把知识文档存在
  `avatar_knowledges`（id/avatar_id/content/...）表，与现在的 N:N 绑定表重名；
  启动迁移会自动先把旧表改名 `avatar_knowledges_legacy`，再把它迁成全局集合
  「旧知识库 #<id>」并重新入库 service-rag，全程非破坏（成功后旧表才删除）。
- **直播推流没画面**：先确认 `docker compose up` 里 srs 健康、`/live/<id>.flv`
  可拉流；ffmpeg 日志在 `stream-<id>/ffmpeg.log`。音频必须与视频交错写（见
  ffmpeg_pipe.py 顶部说明），否则双管道死锁。
- **字幕字体不生效**：确认文件名与 `worker/fonts/` 里的文件一致（含扩展名），
  缺文件会自动回退系统默认字体并打 warning 日志。
- **闲置/说话切换崩溃**：两种状态的帧率与分辨率必须一致（都来自同一 base 片段）；
  视频输入带 `-re` 实时节流，勿删除。

- **口播媒体缓存（2026-10-28）**：IndexTTS WAV 和 LatentSync MP4 缓存于 RustFS `cache/resident/v1/`，按台词/音色/模型指纹和素材身份复用，切段视频逐段命中。更新模型配置需重启常驻模型服务。详情见 `docs/resident-media-cache.md`。

- **自主主持（2026-10-28）**：Live Studio HostPanel + 管理员 `/live/:avatarID/host` 接口，Redis 保存分段计划/控制/已讲进度。`worker/ai/host_director.py` 最多预备两段，手动口播队列优先，在当前短段结束后插播并继续。当前模型请求不硬中断；小红书示例已观察进入提示 DOM，但尚未自动采集或回复。见 `docs/autonomous-host.md`。

- **可配置无头弹幕采集（2026-10-28，更新）**：Live Studio `XHSListener` + `/api/live/:avatarID/xhs` 保存本场分享链接、监听开关和欢迎/回复开关。`worker/xhs_collector.py` 作为独立 systemd 用户服务通过 Playwright 读取网页新增 DOM；不发送小红书网页评论。手动队列优先于 `live_external_queue:{id}`，后者优先于主持。旧房间事件通过 revision 失效。遇到登录或验证显示异常，不自动绕过。本文前述“尚未自动采集”已由本项替代，详见 `docs/xhs-listener.md`。

- **采集位置更新**：用户选择本机有头浏览器，成都出口小红书返回 300012。使用 deploy/install-local-collector.py 安装本机 LaunchAgent 与 loopback SSH 隧道，XHS_BROWSER_HEADLESS=0；成都默认禁用采集服务，仍负责回复/音视频。公网不新增端口。详情见 docs/xhs-listener.md 顶部当前部署说明。

- **Chrome 插件采集（2026-10-28）**：`extensions/chrome` MV3 插件，通过 activeTab 手动采集本场页面 DOM，service worker 带绑定数字人的 12 小时 Bearer 密钥 POST `/api/collector/session|events`。后台提供下载/生成/撤销/停止/事件展示；默认只采集，欢迎和回复需用户在插件开启。原 Playwright 采集服务继续禁用，client=extension 时旧 Collector 跳过。无新端口。代码变动后需 `python3 extensions/package_chrome.py` 同步 ZIP；验证与边界见 docs/chrome-collector.md。

- **成都流式直播（2026-10-29）**：当前生产直播切换为 Qwen SSE分句 → CosyVoice3（loopback59495）→ MuseTalk逐帧（loopback59496）→ 原FFmpeg/SRS。仅知夏2、若棠3有准备好的profile，见deploy/musetalk-avatars.json；素材不匹配禁止换脸兜底。模型GPU2、常驻预热，参考/环境沿用独立实验；缓存完整音视频事件，重复话术不重复生成。Chrome采集欢迎和回复各2秒限频，保留去重与有界队列。旧模型保留用于回滚；禁止重新启用Playwright。发布与回滚、私有端口、验证详见docs/cosyvoice-musetalk-live.md。

- **互动优先级（2026-10-29）**：手动口播 `live_queue:{id}` → 评论回复 `live_external_queue:{id}` → 欢迎 `live_welcome_queue:{id}` → 自主主持。评论与欢迎独立检查队列容量，欢迎满不挡评论；在取下一任务时生效，不硬打断已开始口播。停止、换房和撤销采集密钥清空两种外部队列。当前Chrome插件真实欢迎与评论回复已验收；上述旧的Playwright/尚未采集描述仅作历史，不重新启用。详见docs/chrome-collector.md和docs/cosyvoice-musetalk-live.md。

- **点赞/礼物感谢（0.1.3）**：Chrome结构化like/gift事件，thanks默认关闭。手动口播 → 礼物live_gift_queue → 点赞live_like_queue → 评论live_external_queue → 欢迎live_welcome_queue → 主持；点赞15秒合并限频/队列1，礼物2秒限频/队列3。感谢固定话术，沿用防注入，所有停止/换房/关播/撤销清理新队列。定向更新API、前端、Worker与插件，禁止为此重启模型；细节见docs/thanks-priority.md。

- **点播小节目**：笑话/夸夸/晚安精确短请求走固定稿（每类30条，Redis跨API按场次轮换去重）、原评论队列，不经Qwen。用户已放弃连续会话，未采集用户ID或新增个人记忆。见docs/viewer-interaction.md；发布仅更新相关API，勿重启模型。

- **默认采集密钥（插件0.1.4）**：用户指定前后台默认REDACTED_COLLECTOR_KEY、长期有效，插件数字人ID默认3，通过X-LingCast-Avatar-ID绑定。Redis停用标记可撤销默认密钥，后台重新启用；保留旧随机密钥兼容。与管理员登录密码无关。详情docs/chrome-collector.md。

- **ID3换装**：精确「换衣服/换一套衣服」触发白衣→酒红→香槟轮换。当前讲话结束后，严格intro完整口播→静音离场/空镜/入场→writer提交新idle/renderer并先显示idle→outro口播→释放互动队列。每方向三个文件，失败不使用未准备造型，重新开播恢复白衣。见docs/wardrobe.md。
- **主持换装提醒**：ID3主持话术库三条固定提示，4分钟限频且至少隔3段普通主持；插件采集/回复、完整manifest和全部口型ready才提醒，不自动换装。旧任务需重新启动话术库。
- **发布边界**：新造型3002/3003使用loopback59497独立wardrobe服务，原白衣/知夏仍走59496并保留缓存。原CosyVoice、MuseTalk、IndexTTS、LatentSync不得为此重启；只定向API/前端/stream Worker，不跑全量chengdu.sh。runtime/wardrobe.json为实际清单，仓库示例仍默认关闭；59509是本地预览，实际验证证据另见runtime/wardrobe-verification。
