# 小红书互动采集

当前生产使用用户本机 Chrome 插件，服务器负责 Qwen → CosyVoice → MuseTalk 及播放。操作、限频与队列说明以 [Chrome 插件采集](chrome-collector.md) 为准；插件服务器固定为后台59480。手动口播 → 评论回复 → 欢迎 → 自主主持，评论与欢迎独立排队。

以下是保留的 Playwright 采集方案记录，当前已停用，不作为恢复生产的操作步骤。

## 历史：本机可见浏览器采集

用户已选择本机有头浏览器采集，成都服务器负责 Qwen、配音、口型和播放。成都出口访问小红书返回 300012（IP 风险），因此默认部署会禁用服务器采集服务。没有绕过验证码或平台限制。

本机执行 `cd worker && uv run --group resident-runtime playwright install chromium --no-shell`，再执行 `uv run python ../deploy/install-local-collector.py` 安装两个登录启动的 LaunchAgent：`com.lingcast.collector-tunnel` 和 `com.lingcast.xhs-collector`。SSH 只在本机 127.0.0.1:59489 转发成都 loopback Redis 59479，公网仍只有原后台 59480；采集端 API 使用后台地址。可通过安装脚本参数调整主机别名、后台地址和本地端口。

后台开启监听时，本机才会打开独立浏览器窗口；登录或验证由用户直接操作。独立资料目录 `runtime/xhs-browser/avatar-{id}` 保留浏览器会话，目录权限 700，不读取个人 Chrome 配置。关闭该窗口会停止本轮采集并显示异常，需要在后台重新开始；本机休眠/退出登录会中断采集。日志在 `runtime/local-collector/`。停止常驻采集端可执行 `launchctl bootout gui/$(id -u)/com.lingcast.xhs-collector`，隧道对应 `com.lingcast.collector-tunnel`。如更换代码目录，重新运行安装脚本。

主程序读取 `XHS_BROWSER_HEADLESS=0` 使用可见持久浏览器；显式设置 1 可在可用网络的服务器使用无头模式。只有运行 `XHS_COLLECTOR_LOCATION=server bash deploy/chengdu.sh` 才会部署启动服务器采集端；默认 local 禁用它，避免两处重复监听。

## 历史采集器的消息处理与接口

直播台「小红书弹幕监听」中的链接用于采集浏览器读取网页，和 RTMP 推流配置相互独立。开播后粘贴本场完整 `xiaohongshu.com/livestream/...` 地址或 `xhslink.com` 分享文案，再点击「保存并开始监听」。每个数字人分别保存配置，刷新、服务重启后保留；更新房间或开关后重新点击开始会关闭旧页面、打开新页面并重新建立历史消息基线。

自动欢迎和 Qwen 回复默认关闭。可以先只监听，确认最近消息正常再按需要开启。采集器不会向小红书网页发送文字；回复只进入本系统数字人口播。数字人未开启时仍可采集，事件不会补排口播。只打开一个浏览器进程，每个房间独立上下文，最多同时四个房间，不读取个人浏览器的登录 Cookie。

页面初始化等待输入区域出现并等待历史加载，随后用 MutationObserver 读取新增 `.msg-wrapper.role-user`。过滤 `.role-host`、系统通知、点赞、分享和礼物；昵称取 `.nickname`，正文取 `.content`，图片表情用 alt 文本。`.enter-msgs` 中“来了”识别为进场。空房间也可以等待新消息。仅依赖可见网页 DOM，没有逆向私有 WebSocket 协议。

限流：事件 ID 去重、30 秒过期；相同观众 5 分钟内重复进入不重复欢迎，同房间每分钟最多一次欢迎；每 12 秒最多回复一条，同文 15 秒内重复不触发。最近事件最多保存 100 条、保留一天，页面展示当前配置修订的最近 40 条。网页重新渲染、断线重连时基线会重建，可能略过刚发生的事件；不能宣称平台级消息无丢失。

当前共享调度优先级：`live_queue:{id}` 手动口播 → `live_external_queue:{id}` 评论回复 → `live_welcome_queue:{id}` 欢迎 → 自主主持。当前播放段和当前模型请求会完成，再处理新任务。外部事件携带配置 revision，停止或换房后旧队列被清空，尚在生成的旧回复也会在入队/消费阶段校验 revision，避免播到新房间。外部队列生成回复仍受已有队列容量 5 条限制，采集端有额外低水位检查与最多两个并行操作。

管理员接口：`GET/PUT /api/live/:avatarID/xhs`，字段 url、enabled、welcome、reply。返回 config、当前心跳 status 和最近 events。完整分享 token 只出现在管理员配置中，不写入采集日志。普通消息入口接受 source=xiaohongshu、roomRevision，仅在对应房间启用回复且 revision 匹配时受理，回复使用原 Qwen/聊天记录链路。

部署使用 `deploy/lingcast-xhs-collector.service` 独立运行 `worker/xhs_collector.py`，没有新增监听端口。Playwright 加入锁定的 resident-runtime 依赖组；`deploy/chengdu.sh` 在停服务前安装匹配的 Chromium Headless Shell 并预检启动，失败时保留现有直播进程。Ubuntu 若缺系统动态库，需要根据 Playwright 报错安装对应系统依赖，再重试发布。后台状态包括连接中、监听中、需要登录/验证、直播结束、连接异常；不会自动绕过验证码。发生异常需人工检查并重新开始，不无限刷新房间。

测试：`cd worker && uv run --group resident-runtime python -m unittest discover -s tests`，浏览器 DOM 测试覆盖首屏历史、主播/点赞过滤、表情和新进场；策略测试覆盖去重、限流、过期和手动优先/旧房间回复丢弃。Go 测试覆盖分享文案解析、完整 query 保留、拒绝非直播页/非小红书域名。
