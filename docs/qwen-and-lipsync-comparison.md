# Qwen 与现有数字人接入（2026-10-28）

后续已完成 IndexTTS + LatentSync 常驻互动接入，当前运行与恢复方式见 [resident-live.md](resident-live.md)。本文下方保留首次对比的历史测量与当时边界。

本次沿用 LingCast 的 API、数据库、Redis、S3 与场景视频结构。未接入小红书弹幕，未启动对外直播。

## Qwen

在未入库的 `.env` 中配置：

```dotenv
OPENAI_API=chat_completions
V2_QWEN_BASE_URL=http://your-qwen-host:18003/v1
V2_QWEN_MODEL=qwen35
V2_QWEN_API_KEY=your-private-key
```

`V2_QWEN_*` 优先于 `OPENAI_API_KEY/BASE_URL/MODEL`。不配置 Qwen 时保留原 Responses 模式。Docker Compose 的 api-live、api-admin 均传递模型配置，修改后重建/重启对应服务。

两条聊天入口 `/api/live/:avatarID/message`、`/api/live/chat` 都支持 Chat Completions；仍保留房间上下文、人物设定、RAG 与动作视频提示。Qwen 禁用思考（`chat_template_kwargs.enable_thinking=false`），仅取 `message.content`，遇到空文本、截断或显式思考标签跳过配音。请求最长 30 秒。模型调用失败不再回读观众原话。

当前 Qwen 分支使用完整回复，之后按原逻辑分句入队；尚未实现模型 token 到 TTS 的实时流式传递。实际 `/message` 测试结果在 `tmp/lipsync-comparison/qwen-api-test.json`。

## 素材导入

通过正常管理 API 上传，媒体存入 LingCast 自己的 S3，不依赖原项目文件目录持续可用。原人物文件不修改。

```bash
cd worker
uv run python import_existing_avatar.py --api http://127.0.0.1:59480 \
  --portrait /Users/taoxin/my-videos/assets/presenters/it-host-v1/portrait.png \
  --video /Users/taoxin/my-videos/assets/presenters/it-host-v1/base-h3.mp4
```

导入脚本从根 `.env` 读取管理员凭据，不打印密码，按名称复用人物、按描述复用待机视频。默认音色仍为 Edge-TTS；此次 IndexTTS 使用同一句话独立合成公共测试音频，未冒充已经接上生产 TTS 服务。

本机 8080 已被占用，测试管理端使用 `.env` 的 `APP_PORT=59480` 与对应 `S3_PUBLIC_BASE_URL`。管理员账号为 `admin`，当前密码以本地 `.env` 为准；用户已要求通过账号接口调整，修改环境变量不会覆盖已初始化的数据库密码。现有 DB、Redis、S3 使用本项目独立的 Compose volumes。

## 对比方法

台词：你好，欢迎来到直播间！有什么问题，都可以在聊天区告诉我。

- IndexTTS 2.5 使用既有 voice_09、ZH、duration_factor=1.0，生成一份共用 WAV。
- 统一底片 576×768、25fps；音频 16kHz、单声道，前置静音 0.4 秒、尾部静音 1 秒。
- Wav2Lip 使用 LingCast 的 ONNX 实现、batch=8、不启用人脸增强；在当前 M5 上测冷、热两次。
- LatentSync 复用云端现有独立进程 runner，在空闲 GPU 4 上测试，20 steps、guidance=1.5、seed=1247。复用既有 GPU 文件锁，不修改原服务。
- 对比前核验两种引擎输入音频、视频的 SHA-256；离线测试 fixture 通过 SSH 传输，不改变应用跨服务只用 S3 的约定。
- 模型下载、安装、网络复制不入口型计时。Wav2Lip 记录首个内存帧、生成与写盘、最终 MP4；LatentSync 当前只有整片输出。

这是 M5 本地与 4090 云端两种部署方案的实测，不是同硬件算法排名，也不是从观众发言到小红书观众看见画面的端到端延迟。单句样本不代表长播稳定性，不给未经计算的同步分数。

## 复测入口

```bash
cd worker
uv sync --group benchmark
uv run --group benchmark python benchmark_lipsync.py \
  --video ../tmp/lipsync-comparison/face-input.mp4 \
  --audio ../tmp/lipsync-comparison/speech-input.wav \
  --out ../tmp/lipsync-comparison
uv run --group benchmark python build_comparison_report.py
uv run python -m http.server 59321 --bind 127.0.0.1 --directory ../tmp/lipsync-comparison
```

远程 helper `worker/benchmark_remote.py` 需在既有 IndexTTS/LatentSync 环境运行；它不会安装或改动远程依赖。正式模型仍不入库。

存储桶初始化现已改为官方 `rustfs/rc:latest`，不再依赖 MinIO 客户端镜像。已验证既有桶的重复初始化、新桶创建和匿名对象下载；临时测试桶已清理。可运行 `docker compose up rustfs-init` 初始化存储桶。其他前端、知识库、TTS 服务及直播 Worker 尚未全部部署验证。

## 本次结果

| 实际运行方式 | 首个内存口型帧 | 完整 MP4（不含 TTS、网络） |
| --- | ---: | ---: |
| Wav2Lip，M5，首次冷启动 | 30.39 秒 | 34.02 秒 |
| Wav2Lip，M5，同进程第二次 | 1.57 秒 | 4.27 秒 |
| LatentSync，4090，现有独立进程 | 无流式回调 | 51.13 秒 |

Wav2Lip providers 为 CoreML + CPU。冷启动含音频处理依赖、模型与执行器首次初始化；不是稳态性能。热运行生成并写盘约 3.91 秒，含最终 MP4 封装为 4.27 秒，输出时长 6.48 秒。LatentSync 输出时长 6.40 秒。TTS 原始音频约 5.22 秒，前后加静音后约 6.62 秒；两引擎都截去了部分尾部静音，输出时长覆盖正文语音。

两份输出经 ffmpeg 完整解码通过；输入哈希一致。已抽查 0.2、1.4、2.8、4.6 秒画面：LatentSync 嘴周细节和脸部纹理更清楚；Wav2Lip 下半脸偏软，个别张嘴帧的牙齿/口腔形态较生硬。此为单句抽帧判断，未做 SyncNet/LSE、真人参照或用户盲测，不能宣称客观同步评分已验收。

建议互动链路优先验证预热后的 Wav2Lip，LatentSync 用于高画质预生成片段。尚需在真实持续推流中确认首句延迟、音画同步和长播稳定性。

对比页面：http://127.0.0.1:59321/ ；管理端：http://127.0.0.1:59480/ 。输出、原始日志、metrics、导入记录保存在 gitignore 的 `tmp/lipsync-comparison/`。重启后页面服务需按前述命令启动，管理端可用 `docker compose up -d --no-deps api-admin api-live api-scheduler frontend-admin` 恢复（基础设施须已启动）。
