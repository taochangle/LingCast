# 部署与准备

当前生产链路为Qwen SSE → CosyVoice3 → MuseTalk 1.5 → FFmpeg/SRS。仓库和Compose不包含模型权重、参考声音、预处理人脸或私有配置，不能仅启动Compose就获得完整GPU直播能力。

## 环境与版本

- Git、Docker Engine / Compose、Go 1.25、pnpm 10.25、FFmpeg、Python及uv。
- Worker项目支持Python>=3.11,<3.14，`.python-version`固定3.12；模型各有独立环境，不同步全部依赖组到已运行的模型环境。
- 当前流式模型在Linux NVIDIA CUDA部署，GPU余量和端口占用须实际检查；其他平台的历史说明不代表本链路已验收。

```sh
git clone https://github.com/taochangle/LingCast.git
cd LingCast
cp .env.example .env
cp worker/.env.local.example worker/.env.local
```

自行填写私有配置并按[流式手册](../cosyvoice-musetalk-live.md)准备环境、权重、音色和形象profile。禁止把模板中的其他工作流选项当成当前生产默认值。新增底片必须匹配对应预处理数据，不能套用另一人物的脸。

## 启动与验证顺序

1. 准备MariaDB、Redis、RustFS、SRS及相关API/前端，确认内部访问。
2. 启动并预热独立模型服务，验证`/readyz`，进程active不等于模型可用。
3. 启动原生直播Worker，核对API/Worker版本与素材映射。
4. 管理员登录后，在Live Studio验证待机 → 手动原文口播 → 结束回待机 → 重复口播缓存命中。
5. 按需手动启动[Chrome插件](../chrome-collector.md)，再开启外部推流。房间采集地址与RTMP目标是两项不同配置。

生产只开放必要后台网关；模型、队列、对象存储及SRS内部入口保持内网/loopback。具体映射见[部署文档](../chengdu-deployment.md)，地址和密钥只放私有环境文件。管理员初始化信息使用独立强密码。

## 更新边界

文档只同步Git文件，不构建或重启。插件只重新打包/加载相关产物。业务只部署相关服务，模型重启需明确授权；不要为普通更新执行全量部署脚本。更换造型先准备人脸、缓存与清单，再按[换装发布与回退](../wardrobe.md)切换。

完整步骤见[仓库README](https://github.com/taochangle/LingCast#readme)。旧流程仅在[历史归档](../history/legacy-readme-zh.md)中查阅，不作为当前执行指令。
