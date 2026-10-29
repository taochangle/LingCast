# LingCast MuseTalk 常驻流式实验

**独立测试，不注册生产 API/Redis 直播队列，不改变现有模型/推流配置。**

在已有独立环境 `/data1/taoxin/lingcast-musetalk-bench` 中运行，官方 MuseTalk v15 固定源码与已预处理的知夏/若棠素材沿用前次实验。`model.py` 加载模型和两个人物，启动时逐人处理一次样例音频；只有预热完成 `/readyz` 才返回 ready=true。物理 GPU2，Torch 分配上限20%。

`server.py` 只监听 `127.0.0.1:59494`。读取现有 Worker 私有环境，复用 IndexTTS HTTP 服务及配音缓存（测试新句会占用现有 TTS 服务一次推理，但不修改配置），不调用 LatentSync、不触发线上口播。输入限100字，每次只处理一个任务，本轮进程最多30个任务；结果在实验目录 `live-test/<uuid>/`。额外 Python 依赖 boto3，安装在实验 .venv 内。

```
cd /data1/taoxin/lingcast-musetalk-bench
nohup .venv/bin/python musetalk_live/server.py > live-test.log 2>&1 < /dev/null &
# 本机另开 SSH 隧道；不发布新的公网端口：
ssh -N -L 127.0.0.1:59494:127.0.0.1:59494 chengdu-taoxin
```

本机浏览器访问 http://127.0.0.1:59494/。不要同时启动两个测试进程；需要退出时仅终止本实验 server.py 的 PID，不停止生产服务。

## 流程和测量

提交 JSON → IndexTTS 整句生成/命中音频缓存 → 下载WAV → MuseTalk小批推理与回贴 → JPEG通过SSE持续传送 → 浏览器ImageBitmap/canvas按WebAudio时钟显示。WAV在服务端完成后浏览器即下载解码；第一张视频与音频都就绪后开始播放，而非等待整段MP4。音视频媒体不再经过视频封装、S3视频上传下载或HTTP-FLV播放器缓冲；因此此结果不能当作未来正式RTMP/小红书观众端延迟。

- 服务端 `tts_ready_ms`、`server_first_frame_ms`、`generation_finished_ms` 使用同一 perf_counter，从接收任务开始计时。
- 浏览器 `browser_first_frame_ms` 从点击提交（AudioContext激活之后）到首张口播画面绘制后的下一次requestAnimationFrame。时间包含网络、TTS、音频下载和浏览器图像解码。
- `browser_audio_start_ms` 是WebAudio计划播放起点；`browser_first_speech_ms` 在此基础上加WAV首个绝对幅值>0.01样本的时间，作为非静音语音起点估计。**不是麦克风回录或嘴部检测得出的物理开口时刻**，不含硬件扬声器额外延迟。
- `frames` 是画布实际更新次数，可能低于生成帧数（按音频时钟丢弃过期帧），不等于模型FPS。
- 结果有缓存命中/未命中标志；新句仍需整句配音，不能用配音缓存成绩冒充新回复延迟。

测试页面会将浏览器指标写回本次实验目录的 browser.json，服务端指标在 metrics.json。模型保持常驻，页面关闭不停止模型进程。本实验是实时画面交付与计时验证，尚未接正式推流、支持打断、流式TTS或生产认证接口。

## 2026-10-29 浏览器实测快照

同一测试常驻进程，8条已完成任务（包含页面手动测试）：3条配音未命中，5条命中。正文27～29字；不是大样本或p95统计。

| 指标 | 未缓存新句（3条） | 配音缓存命中（5条） |
|---|---:|---:|
| 提交到浏览器首张口播画面，中位数 | 3.692秒 | 0.691秒 |
| 提交到非静音语音起点估计，中位数 | 4.047秒 | 1.033秒 |
| 首张画面范围 | 3.195～9.116秒 | 0.614～0.919秒 |

知夏首条新句 IndexTTS 配音8.605秒，首张画面9.116秒，服务端整段画面到12.040秒才生成完成；若棠新句配音3.189秒、首张画面3.692秒、整段画面6.346秒完成。这验证了首帧先于整段完成输出。新句配音波动明显，下一阶段若追求稳定低延迟，需要单独评估IndexTTS首包/流式配音，而非仅继续优化口型。没有测试小红书、RTMP或HTTP-FLV观看延迟，也没有声学回录来校准扬声器延迟。

原始服务端与浏览器指标保存在服务器实验目录 live-test/results.json，本机 runtime/musetalk-live-results/results.json。生产线上版本核对仍为 b4eacad；本实验代码独立提交，不执行生产 deploy/chengdu.sh。
