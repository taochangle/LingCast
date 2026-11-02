---
layout: home
hero:
  name: LingCast
  text: 灵播 · 互动数字人直播
  tagline: Qwen流式回复 → CosyVoice3配音 → MuseTalk 1.5逐帧口型 → FFmpeg / SRS，支持手动口播、自主主持和换装。
  image:
    src: /images/logo.svg
    alt: LingCast
  actions:
    - theme: brand
      text: 部署与准备
      link: /guide/start
    - theme: alt
      text: 当前功能
      link: /guide/features
    - theme: alt
      text: GitHub
      link: https://github.com/taochangle/LingCast
features:
  - title: 流式互动
    details: Qwen完整短句入队，配音块与口型帧配对播放；手动口播原文直说。
  - title: Chrome采集
    details: 用户主动启动DOM只读采集，独立去重、限频、感谢与回复队列。
  - title: 自主主持
    details: 固定话术库或自定义一轮讲稿；首条就绪即可开讲，互动结束继续。
  - title: 换装与缓存
    details: 已准备造型按完整阶段换装，底片与口型身份绑定，重复话术复用音视频缓存。
  - title: 管理与知识
    details: React管理台、Next.js观众端、Go API、MariaDB、Redis、RustFS及zvec/Jieba检索。
  - title: 部署边界
    details: 当前GPU流式模型使用Linux NVIDIA CUDA及独立环境；只发布必要后台网关。
---
