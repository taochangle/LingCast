# CosyVoice3 独立实时实验

不部署生产 API、不接 Redis 直播队列、不修改 IndexTTS/LatentSync 服务或线上推流。

## 隔离部署

- 成都目录：`/data1/taoxin/lingcast-cosyvoice-bench`，独立 Python 3.11 uv 环境。
- CosyVoice 仅监听 `127.0.0.1:59495`；监看沿用独立实验 `127.0.0.1:59494`。
- 不创建 Docker 公网映射、不改防火墙、不开放模型公网端口。
- 物理 GPU2，与独立 MuseTalk 同卡；不使用生产 IndexTTS/LatentSync 所在 GPU4。
- ONNX 参考音频编码使用 CPU，Torch 模型使用 GPU；Torch 分配上限14%。

源码采用官方仓库固定快照，而非跟随 main：

- `QwenAudio/CosyVoice`: `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`
- `shivammehta25/Matcha-TTS`: `dd9105b34bf2be2230f4aa1e4769fb586a3c824e`
- `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`: `29e01c4e8d000f4bcd70751be16fa94bf3d85a18`

源码在 `source-pinned/`，Matcha 放在其中 `third_party/Matcha-TTS/`；模型在 `model/`。
只下载推理所需权重，未下载 RL/批量 tokenizer/TensorRT 导出权重。本轮使用原生 PyTorch FP16，未启用 vLLM、TensorRT 或 DeepSpeed。适配器固定 `token_hop_len=25`、`stream_scale_factor=1`，避免上游预热后跨请求保留放大的块长度；不改上游源码。
`pyproject.toml` 与 `uv.lock` 管理独立依赖，不修改生产 Worker 环境。此次下载慢的三个大包从本机下载官方 PyPI wheel、校验官方 SHA256 后传入服务器安装，版本与锁文件一致；当前环境启动用 `--no-sync`，避免 uv 因本地 wheel 的来源标记重新下载同版本。可用 `uv pip check` 检查依赖完整性。

现有 IndexTTS 的知夏、若棠共用 `index-tts/examples/voice_09.wav`。
实验将其复制为 `prompt.wav`，配合 Whisper 本地转写的 `prompt.txt`（尚未人工校对），启动时计算并缓存声音条件；预热后才 ready。
不假定两个人物已拥有不同声音。

```
cd /data1/taoxin/lingcast-cosyvoice-bench
# 仅首次创建全新环境时执行；当前环境已安装并校验：
# ~/.local/bin/uv sync --frozen
nohup ~/.local/bin/uv run --frozen --no-sync service.py > service.log 2>&1 < /dev/null &
curl http://127.0.0.1:59495/readyz
# 本地通过 SSH 访问监看，CosyVoice 端口无需转发：
ssh -N -L 127.0.0.1:59494:127.0.0.1:59494 chengdu-taoxin
```

已有实验进程时不要重复启动。停止时仅终止本实验 PID，不能终止其他 GPU 进程。
服务启动前先绑定端口，冲突时直接失败，不先加载另一份模型。

## 数据链路

监看选择 Qwen 流式或直接口播，再选择配音引擎。
Qwen 首句完成后就发给 CosyVoice，后续正文继续接收；CosyVoice 输出原生24kHz float32 PCM 音频块。
HTTP 使用 chunked NDJSON 即时交付，不等待整句 WAV；完整 WAV 另行保存用于音色试听。

MuseTalk 音频核心窗口1秒，两侧各保留160ms上下文；只播放核心区声音、只传核心区画面。
人物画面、潜变量和遮罩一起连续偏移，避免每块重置人物姿态。
下一块音视频就绪后在 WebAudio 时钟提前排程，已准备好的块连续播放；供给不足仍会等待。
`audio_gap_ms` 记录排程空隙累计，不能代表物理声学连续性。
最后不足1秒的尾块原样保留，不重复或丢失 PCM 样本（有单测）。不足一帧的尾音仅补渲染上下文，播放音频不补齐。MuseTalk 启动时额外预热24kHz短窗口，避免首个 CosyVoice 请求才触发音频重采样冷路径。

对比模式勾选「跳过配音缓存」：IndexTTS 每次重新推理、写入独立 benchmark S3 Key，CosyVoice 每次重新推理。
两者使用相同文本、相同参考音频，但不同生成模型不保证相同语速或时长。
新增 PCM 交付限于本机实验服务，生产媒体/S3协议保持原状。

## 测量与试听

浏览器首帧为 canvas 绘制时间；语音起点为 WebAudio 排程加首个绝对幅值>0.01样本的位置，非麦克风实录或嘴部检测。
新播放器首帧对齐音频播放时钟，不能把旧播放器提前显示的首帧数直接当同条件比较。
测试不包含 RTMP、小红书或正式 HTTP-FLV 缓冲。

运行 `uv run --frozen --no-sync report.py <job_id> ...` 生成私有 `/comparison` 页面与 `live-test/cosyvoice-comparison.json`。
页面提供相同台词的完整音频试听和原始计时；完整音频试听用于音色判断，分块连续性要在互动页观察。
音色相似度、语气和自然度需要听辨，不用延迟数字替代音质评价。

本地测试：
```
uv run --directory worker python -m unittest discover -s ../experiments/musetalk_live -p 'test_*.py'
```

## 2026-10-29 实测结果

固定台词：欢迎来到直播间，很高兴在这里见到你，今天过得怎么样？

知夏、若棠各2次，每引擎共4次，全部跳过配音缓存。共享服务器当前负载下的端到端样本，不是两张 GPU 完全同负载的纯模型基准。IndexTTS 在 GPU4，CosyVoice 与 MuseTalk 在 GPU2（该卡还常驻其他 VLLM）。

| 指标 | IndexTTS（4条） | CosyVoice（4条） |
|---|---:|---:|
| 浏览器首帧中位数 | 3.14秒 | 2.30秒 |
| 首帧范围 | 2.81～7.90秒 | 2.03～5.65秒 |
| 非静音语音起点估计中位数 | 3.42秒 | 2.45秒 |

CosyVoice首条知夏出现5.65秒首帧、累计203ms排程空隙，后续同句知夏2.18秒、若棠2.03/2.43秒、排程空隙均为0。IndexTTS的一条复测也有7.90秒首帧；服务日志该条总推理6.35秒（GPT4.07秒、s2mel1.14秒），不能把全部等待归因于口型。

这些是少量实测，尚不能保证稳定延迟。两张卡负载、语速、生成长度以及网络/磁盘均可能影响结果；未做人工音色盲评。对比页保留全部8条，不隐藏慢请求。当前结论是 CosyVoice 值得继续实验，尚不替换线上。

另外分别验证知夏、若棠的 Qwen 流式两句回复：

| 指标 | 知夏 | 若棠 |
|---|---:|---:|
| Qwen首句开始配音 | 0.784秒 | 0.169秒 |
| Qwen完整回复结束 | 0.875秒 | 0.261秒 |
| 浏览器首帧 | 2.979秒 | 2.484秒 |
| 非静音语音起点估计 | 3.082秒 | 2.657秒 |
| 全程累计排程空隙 | 429ms | 0ms |

首句均先于Qwen完整回复开始配音，完整回复与后续口型生成重叠执行。知夏仍有短暂停顿，分块链路尚不能承诺无缝。未接线上直播推流。

报告：本机 SSH 隧道 `http://127.0.0.1:59494/comparison`，互动页 `/`。
原始数据在服务器 `live-test/cosyvoice-comparison.json`、`cosyvoice-qwen-integration.json`，本机 `runtime/cosyvoice-comparison/` 同时保留JSON、8条WAV和页面截图。

初次接线时的旧自增块配置及冷路径探测任务（dc6f5eb546824b50b3066049c7706c0c、18d7c8dc05c340f288d5af79ad0ba876、ced4a0f185f84d83972ed13554671a05）保留在服务器，未混入固定25-token配置的上述统计。

运行中的两套实验源码与本地 SHA256 已核对一致。生产代码仍为 b4eacad，生产 IndexTTS/LatentSync/stream 服务保持运行。本实验不执行生产部署脚本。
