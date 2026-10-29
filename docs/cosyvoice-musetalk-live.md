# 成都实时直播链路

生产直播使用 Qwen SSE 分句 → CosyVoice3 PCM 块 → MuseTalk 窗口逐帧 → 常驻 FFmpeg/SRS。
手动口播按原文说，不经过 Qwen；观众互动、Chrome 插件自动回复使用 Qwen 流式。

## 已准备的形象

`deploy/musetalk-avatars.json` 固定知夏（2）、若棠（3）的底片和预处理 profile。
两者均为576×1024竖屏，生产输出25fps、16kHz单声道音频，帧/采样时钟严格对齐。
修改底片或新建其他数字人后，必须先准备对应 MuseTalk profile 和映射；不允许拿另一张脸兜底。
当前旧的 IT 主持人（1）没有 MuseTalk profile。

## 服务与端口

- `lingcast-cosyvoice.service`：`127.0.0.1:59495`，CosyVoice 独立 uv 环境、常驻预热。
- `lingcast-musetalk.service`：`127.0.0.1:59496`，MuseTalk 独立环境、常驻模型与两个人物。
- 两个模型使用物理 GPU2；不改其他使用者的 GPU 分配。旧模型服务保留，便于回滚。
- 只有后台59480对外。RustFS、Redis、SRS和模型均维持内部访问，插件只连接后台。

Worker 私有 `worker/.env.local` 设置：

```
LIPSYNC_ENGINE=musetalk
MUSETALK_RESIDENT_URL=http://127.0.0.1:59496
COSYVOICE_URL=http://127.0.0.1:59495
```

沿用既有 `RESIDENT_API_TOKEN`，不把凭据提交 Git。MuseTalk 接口校验 Bearer 并拒绝浏览器 Origin；CosyVoice仅接受内部客户端。
环境、权重、参考声音路径沿用已验证的 `/data1/taoxin/lingcast-cosyvoice-bench`、`/data1/taoxin/lingcast-musetalk-bench`。
CosyVoice uv 环境已有经过校验的本地 wheel，启动使用 `--no-sync`；不要在该环境无意重新同步并删除 Torch。
依赖版本及模型来源见 `experiments/cosyvoice_live/`。

## 播放、优先级与缓存

Qwen 每出一个完整短句立即入队，不等待整份回复；忽略 reasoning 字段，拒绝思考标签，不完整尾句丢弃。
已播出的句子无法因后续模型失败而撤回。RAG、人物设定、聊天记录继续生效。

CosyVoice 固定25-token小块；MuseTalk核心音频窗口约1秒，带160ms左右上下文。
TTS与口型计算重叠，Worker收到配对的声音与画面就交给原推流管道，不等待完整MP4。
逐窗口播放结束后回到待机。手动口播优先于后续互动和主持，不硬中断正在生成/播放的短段。
主持话术仍可提前准备、循环复用；预备的对象改为流式媒体缓存标记，不走旧 IndexTTS/LatentSync。

`production-cache/` 按文字、人物、底片、模型/参考声音指纹缓存完整音视频事件，最多256项，命中直接播。
命中时不调用TTS/口型，也不等待别的GPU任务；不完整生成不会进入缓存。
换模型、声音或底片会失效，缓存不会跨人物误用。流式PCM/JPEG是本机模型协议，不向公网发布媒体服务。

## 真实弹幕

Chrome插件手动打开直播页，从DOM读取新增昵称、发言、进场提示，不使用 Playwright、不发送小红书评论。
采集密钥在目标数字人直播台「直播互动 · Chrome 插件」生成，12小时有效。
勾选自动欢迎/回复后必须再次点击插件开始按钮应用；数字人直播/监看也须已打开。
欢迎和聊天回复各最多2秒触发一次，同一人5分钟内不重复欢迎；各自队列满时跳过本次触发。
播放优先级为手动口播 → 评论回复 → 欢迎 → 自主主持；欢迎单独排队，不占评论回复容量。已在生成或播放的口播讲完后再取下一项。
2秒是触发限频，不是保证2秒内讲完；不补读历史事件，不让口播积压到失去时效。

## 发布与回滚

本地测试与构建 → 提交推送 → 成都拉取同一提交 → `bash deploy/chengdu.sh`。
脚本先构建镜像、启动并等待新模型预热，再重启直播 Worker；数据库、Redis队列和采集密钥保留。
首次切换前先停止此前单独启动的实验进程，释放59495端口和GPU2上的重复MuseTalk实例。
不要终止无关GPU任务。部署前备份Worker私有配置与原stream systemd单元。

回滚需同时恢复发布前Git版本、Worker私有配置、恢复备份的stream systemd单元并重载systemd，
再按原版本部署脚本发布；单独改Worker引擎会造成API/Worker不一致。

验证：后端流式首句先于完成、错误尾句不发音；Worker配对顺序/音频时钟/缓存不重复生成；
真实直播监看、Chrome上报事件和欢迎/回复分别核对。后台代码版本与原生模型源码均应来自同一提交。
