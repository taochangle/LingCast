# LatentSync 步数对比（2026-10-28）

当前成都服务器默认已从 20 步调为 10 步，配置在 `/data1/taoxin/lingcast-resident/resident.env` 的 `LATENTSYNC_STEPS=10`，由 `lingcast-latentsync.service` 读取。源码保留 20 步兜底值；请求可用整数 `steps`（8–50）覆盖。未改变 guidance、分辨率或 seed。

对比页：http://127.0.0.1:59480/media/talking-avatar/benchmarks/20261028-latentsync-steps/index.html

## 方法和结果

台词：大家好，欢迎来到直播间，今天我们一起聊聊有趣的开源项目。

只生成一次 IndexTTS 音频（5.06195 秒），所有试验复用相同 S3 对象、人物基底、seed 1247、guidance 1.5、512 模型分辨率。输出完整画面 576×768、25 FPS、6.4 秒（包含静音填充和完整 16 帧批次）。先跑一次 20 步预热，再按 20/15/10/8、8/10/15/20 顺序测两轮，取中位数，全部对齐缓存命中。

| 步数 | 请求总耗时中位数 | 相对 20 步耗时降低 |
|---|---:|---:|
| 20 | 24.582 秒 | — |
| 15 | 20.982 秒 | 14.6% |
| 10 | 17.078 秒 | 30.5% |
| 8 | 15.729 秒 | 36.0% |

这里统计口型服务请求总时间，包含视频预处理、推理、人脸回贴、编码及 S3 传输；不包含配音、口播排队、浏览器监看缓冲。每档仅两次，不是跨人物/长文统计。

## 画质判断与选择

四宫格视频、原分辨率独立视频和六个时间点的脸部对齐抽帧均已保留。该样例中各档嘴型、人物身份和细节观感接近，8 步抽帧未见明显崩坏；未计算自动音画同步分数，抽帧也无法替代完整动态/多人物评估。选择 10 步作为速度和余量的折中：8 步比 10 步只再省约 1.35 秒。需要进一步追求低延迟时可复用测试接口对比 8 步。

## 复现与文件

服务器：`/data1/taoxin/LingCast/runtime/steps-benchmark/`。本机对比视频、脸部抽帧及原始测量：`/Users/taoxin/LingCast/tmp/steps-benchmark/`。

```
cd /data1/taoxin/LingCast/worker
/data1/taoxin/.local/bin/uv run --frozen --no-default-groups --group resident-runtime python benchmark_latentsync_steps.py
```

测试脚本单独生成媒体，不入直播队列。测试/服务代码在 `worker/benchmark_latentsync_steps.py`、`worker/resident_service.py`。调整默认值后重启用户服务 `lingcast-latentsync`。模型冷加载约半分钟，计时前必须等 ready；不要把加载时间误算为单条口播延迟。
