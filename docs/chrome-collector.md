# Chrome 插件采集（2026-10-29 更新）

取代浏览器自动化采集：用户在正常 Chrome 中手动打开本场小红书直播页、点击插件开始，隔离世界 content script 使用 MutationObserver 读新增 DOM，service worker 作为客户端将结构化事件推到同一个 59480 后台。没有 Playwright 运行时、浏览器远程调试端口或平台内部接口请求。原采集服务继续禁用；旧 Collector 遇到 client=extension 的配置会跳过，不会打开第二个浏览器。

管理端直播台提供 ZIP 下载、服务器地址、生成/撤销采集密钥、停止采集、连接状态及最近 40 条真实事件。插件已打包但不自动安装，用户需在 chrome://extensions 开启开发者模式并加载解压目录（见 extensions/chrome/README.md）。新增权限仅在插件内由用户手动授权，后台不接受管理员 Cookie 作为采集认证。

## 协议和边界

- 管理员 POST/DELETE `/api/live/:avatarID/collector-token`，生成/撤销绑定数字人的 12 小时随机密钥，Redis 只保存 SHA256，有 TTL。更换密钥会停掉旧扩展会话和旧房间待播队列。
- 插件 Bearer POST `/api/collector/session`，action=start/stop；start 传正式房间 URL、welcome/reply 开关（插件默认均为 false），返回 revision、server_ms、规范化房间 URL。换房间必须新建会话，刷新/关闭页面后需手动重新开始。
- 插件 Bearer POST `/api/collector/events`，最多 20 条/32KiB，每 400ms 至多一批。数据为 id/kind/nickname/text/observed_ms，不含 Cookie、输入框、HTML、分享 token 或其他页面内容。后台从密钥决定 avatarId，不信任客户端指定的数字人。
- 只支持 enter/message，事件过期 30 秒丢弃；客户端握手计算时钟偏差。服务端去重 id 与内容签名、流式模式欢迎和回复各最多2秒触发一次（旧版模式仍为60秒/12秒），评论与欢迎独立限量排队，欢迎排满不会阻止评论触发回复。关播时只展示，不生成口播。旧房间回复会通过现有 xhs_revision 校验失效。
- 自动欢迎走 CosyVoice/MuseTalk 音视频流；回复走 Qwen SSE分句 + 外部口播队列，优先级：手动口播 > 评论回复 > 欢迎 > 自主主持。插件只解决采集，不减少模型计算时间。
- CORS 只对 `/api/collector/` 接受格式正确的 chrome-extension origin，其余管理员接口继续只接受既有后台 origin。所有实际采集请求都必须带有效的限定密钥。
- 插件服务器固定为 http://127.0.0.1:59480 且不可编辑；optional_host_permissions 只声明该主机，不申请所有网页访问；activeTab 只在手动点击时授权当前直播页。密钥存储限制为 TRUSTED_CONTEXTS，不下发 content script，消息来源校验绑定 tab/frame/room/revision。关闭弹窗不会停止采集，关闭 Chrome 会停止。

## 验证与限制

Node 测试覆盖权限声明、事件规范化、来源隔离、固定目标上报与刷新/关闭停止。独立 MariaDB + Redis 集成测试覆盖密钥散列、未认证请求、仅展示、重复事件、旧版本拒绝、欢迎冷却和撤销；CORS 单测验证管理员接口未对扩展放开。DOM fixture 验证首屏跳过、表情、进场、主播/点赞排除、节点复用和停止。2026-10-29 已在沈若棠（ID3）验证真实 DOM 进场、聊天上报、自动欢迎及 Qwen 流式回复口播；后续评论优先调整通过本地和服务器队列顺序测试。实际效果仍依赖当前页面、用户开关和队列容量。

DOM 不是平台正式事件协议，页面结构变更、虚拟列表和后台标签页节流可能漏读；不承诺规避平台限制。HTTP 服务器链路不加密；未来 HTTPS 迁移需同步更新插件固定地址。

## 口播调度

| 顺序 | 队列/来源 | 行为 |
| --- | --- | --- |
| 1 | `live_queue:{id}` | 工作台手动口播、普通互动回复 |
| 2 | `live_external_queue:{id}` | 采集到的评论经 Qwen 分句后的回复 |
| 3 | `live_welcome_queue:{id}` | 进场欢迎原文口播 |
| 4 | 自主主持 | 上述任务空闲后继续 |

优先级在取下一项任务时生效，已生成/播放中的口播会先完成。欢迎和评论的采集准入各检查自身队列，阈值为3；Qwen生成的回复分句入队另受5项上限约束。2秒是分别触发欢迎/回复的最小间隔，不是开口延迟保证。停止、换房或撤销密钥会清空两种外部队列；未入队的旧回复仍按 revision 失效。
