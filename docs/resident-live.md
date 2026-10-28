# IndexTTS + LatentSync 常驻互动链路

2026-10-28 已用原默认 IT 女主持人跑通本地实际 RTMP 输出：

`聊天 → Qwen → Redis → 常驻 IndexTTS → S3 → 常驻 LatentSync → S3 → 完整片段预加载 → 自动插播 → 待机`

## 已运行的入口

- 管理端：<http://127.0.0.1:59480/>
- 实时画面 / OBS 浏览器源：<http://127.0.0.1:59480/obs.html?avatar=1>
- RTMP 拉流：`rtmp://127.0.0.1:1935/live/avatar_1`
- 提交聊天：`POST http://127.0.0.1:59480/api/live/1/message`
- 聊天记录：`GET http://127.0.0.1:59480/api/chat/history?avatarId=1`

OBS 浏览器源可设置 576×768 或同为 3:4 的 1080×1440，并启用浏览器源音频。普通浏览器阻止有声自动播放时点中央播放按钮；`&muted=1` 可用于静音监看。此页只播放直播源，不抢占或消费生成任务。

发送示例（无需把模型密钥暴露给浏览器）：

```bash
curl -X POST http://127.0.0.1:59480/api/live/1/message \
  -H 'Content-Type: application/json' \
  -d '{"username":"小雨","text":"请用一句很短的话欢迎小雨。","senderId":0}'
```

本次只推送到本地 SRS，未连接小红书或其他外部直播平台。

## 服务部署与恢复

两个云端进程分别使用现有模型环境，不向原环境安装或升级依赖：

| 进程 | 模型根目录 | Python | 远端 loopback |
| --- | --- | --- | --- |
| IndexTTS 2.5 | `/data1/taoxin/index-tts` | `.venv/bin/python` | 59411 |
| LatentSync | `/data1/taoxin/avatar` | `.venv-latentsync/bin/python` | 59412 |

模型只在启动时加载。LatentSync 同时缓存人脸模型和最多两个底片的人脸对齐结果。保持 20 steps、guidance 1.5、seed 1247，不用降步数换速度。音色固定 `examples/voice_09.wav`，ZH、duration_factor=1.0。

自有部署目录 `/data1/taoxin/lingcast-resident/` 中保存服务脚本、private `resident.env`、两个 PID 文件、日志和临时工作目录；未改动或停用既有的 17864 服务。当前选用 GPU 4；部署前应检查空闲显存，不能把该编号当成永久专属设备。

首次部署或更新云端脚本：

```bash
cd worker
uv run python deploy_resident.py --gpu 4
```

脚本不会擅自重启已有 PID；更新代码后需有序重启该部署目录里的两个自有进程，重新运行远端 `start.sh`。不要终止其他项目的 GPU 进程。此部署采用 nohup 常驻，不宣称配置了远端开机自动启动或模型进程崩溃自动重启。

恢复本地基础设施、隧道、模型服务及流式 Worker：

```bash
cd worker
uv run python start_resident_live.py
```

入口会复用已在运行的模型和 Worker，数据库中的直播会话会自动恢复。新建会话仍在管理端点击开播。Worker 的本地配置保存在被 Git 忽略、权限 0600 的 `worker/.env.local`；模型密钥保存在根 `.env`，不写入前端 bundle。

隧道方向：本地 59411/59412 → 远端模型服务；远端 59490 → 本地 RustFS 9000。JSON 只携带短时 S3 签名 URL、输出 key 和指标，音视频不经过模型接口响应或 Redis。云端无须持有本地 S3 访问密钥。模型接口检查 Bearer token 和媒体 URL 的固定私有端点。

## 生成与播放

- Qwen 默认只生成一句短回复（最多 35 个中文字或约 25 个英文单词的提示约束）。整条回复作为一个片段，减少重复调用与句间等待。
- 音频加 0.4 秒前置静音；按 16 帧批次上取整并补足尾部静音，生成后检查视频时长覆盖完整语音，避免截句。
- 完整 MP4 下载、解码和音频提取成功后才放入播放队列。生成期间播放原待机底片，失败也继续待机。
- 播放使用单一 FFmpeg 管道。LatentSync 分支按连续视频帧时钟分配音频采样，避免旧版半秒预缓冲在插播边界积累偏移；原 Wav2Lip 分支保留。
- Redis 待处理列表最多 5 条，原子拒绝超容量写入；Worker 丢弃排队超过 120 秒的消息。并不表示已经完成公开平台的垃圾消息治理或多房间压力测试。
- 状态可在 Redis `live_playback:1` 读取：`render`、`playback`、时间、最近片段 S3 key 和生成耗时。模型 `/readyz` 包含 PID 与加载时间，可核实没有逐句重载。

## 实测与验证

同一份原始 5.22449 秒测试音频、同底片、同 20 步配置：

| 方式 | 完整 LatentSync 输出 |
| --- | ---: |
| 原独立进程 runner | 51.13 秒 |
| 常驻模型 + 对齐缓存 | 28.08 秒（含服务端 S3 下载/上传） |

常驻结果中纯 pipeline 约 23.78 秒、输出 6.40 秒；本地取回约 28.44 秒。不能把它称为秒级实时口型。首次启动和首次底片对齐仍有额外开销，底片在开播时预热。

实际聊天测试：Qwen 回复“欢迎小雨来到直播间，一起探索有趣的科技世界吧！”。完整生成和传输 24.77 秒，提交后约 26 秒开始插播；口播约 5.1 秒后自动回到待机。原始状态事件、生成指标和 RTMP 录制保存在 `tmp/resident-live/`。

- `live-proof.mp4`：约 38 秒完整推流录制。
- `live-proof-excerpt.mp4`：从原录制 27 秒处截取的待机/插播/待机节选，约 11 秒。
- `live-events.json`：生成、播放、回待机状态转换。
- `same-audio-metrics.json`：同音频常驻对比。
- `live-contact-sheet.jpg`：实际推流抽帧。

音频检查：生成片段首段静音约 0.683 秒；录制中的字幕/片段起点约 29.333 秒，首个实际口型帧晚一帧，声音约 30.058 秒开始，和片段内部时间对应。此项核对的是插播前后的时间线，未计算 SyncNet 等客观口型同步评分。

已验证 Go 全部测试、真实 Redis 并发队列容量、按帧音频封装、常驻多次请求、输入 URL 边界、鉴权、过期消息丢弃，以及缺失媒体后仍保持待机。浏览器已确认实时播放源可用。长播、多用户并发、小红书弹幕及外部推流尚未验收。

生成的 S3 片段与本地 metrics 暂保留用于审阅；正式长播前需补生命周期清理。模型加载失败或进程退出可通过 readiness 和自有日志定位，再运行恢复入口。
# 直播台外部推流配置

## GitHub 项目速读（简单版）

工作台「直接口播」输入框同样走 `/api/live/:avatarID/narrate`：用户输入即主播台词，不调用 LLM、不生成问答回复。限 220 字，自动分段；下方观众互动记录仍展示观众聊天历史。

直播台新增仓库 URL、讲解重点、可编辑讲稿与读取依据。`POST /api/projects/read`（管理员）通过固定 GitHub API 域名读取公开仓库默认分支 HEAD，固定提交 SHA，再读取目录与最多 7 个文件（每文件最多 5500 字符节选）；不 clone、不运行代码，不接受私有仓库令牌或任意网址。GitHub 匿名额度不足会返回错误。只读部分文件，不能当作完整审计。

Qwen 单独使用项目讲解提示词和 token 预算，不套用聊天的一句话限制。超过 220 字自动压缩。预览不会发声；点击「按原文讲解」才调用管理员 `POST /api/live/:avatarID/narrate`。讲稿限 220 字，拆成最多 5 段、每段最多 50 字，以遵守常驻口型服务 20 秒音频限制。全部片段原子入队，队列不足时整段拒绝；计划口播不使用聊天的 120 秒过期机制。每段生成时待机，完成后播放；简单版尚未实现整场预生成、暂停、跳过或演示画面。需要最新 api-admin、frontend-admin；worker 沿用既有解析器，无需改模型服务。

管理端 `/live-studio` 的「平台推流配置」支持服务器地址、替换串流密钥、仅保存、保存并开始、停止对外推流。先打开数字人直播，再开始对外推流。每次平台新开直播后粘贴当场完整密钥（包含查询参数）。密钥留空沿用旧值，管理员 GET 接口不返回密钥。

管理员接口：`GET/PUT /api/live/:avatarID/relay`。配置按数字人存于 Redis AOF 的 `live_relay_config:<id>`；原生 `stream_worker.py` 内的 `RelayManager` 管理 FFmpeg 转推。状态 `live_relay_status:<id>` 10 秒过期，区分 stopped/waiting/running/error；running 仅表示进程运行，最终接收效果须在平台确认。连接失败后不持续重试，更新配置或再次点击开始可重试。仅保存会停止已有的对外推流，本地数字人直播不受影响。

默认编码：720×1280、30 FPS、H.264 main、3000 kbps、1 秒 GOP、AAC 128 kbps。密钥不写入应用日志；配置与操作接口均需管理员身份。更改 worker 代码后需重启原生 worker。

