# LingCast Chrome 互动采集 0.1

1. 后台直播台下载 ZIP 并解压，或直接使用仓库的 `extensions/chrome` 文件夹。
2. Chrome 打开 `chrome://extensions`，开启开发者模式，加载已解压的扩展程序，选择包含 manifest.json 的文件夹。
3. 在目标数字人的直播台生成采集密钥（12小时有效），插件填写后台服务器地址与密钥。
4. **手动打开自己的小红书直播页**，完成正常登录，点击插件「在当前直播页开始」。分享短链接请先让浏览器跳转到正式直播页。
5. 默认两个开关都关，只采集展示。确认昵称/内容正确后，再按需勾选自动欢迎、自动回复，并点击开始重新应用设置。
6. 插件停止、后台停止或撤销密钥均可停止互动。刷新直播页、切换房间或重启 Chrome 后需要重新开始，跳过首屏历史。

只读取 DOM 中的昵称、聊天文字和进场提示（表情 img alt 转文字）；不读取 Cookie/密码/输入框，不发小红书评论，不请求平台内部接口。读取脚本运行在 Chrome 隔离环境，密钥只存插件可信上下文，由 service worker 发到固定的 LingCast 采集接口。没有 remote code、外部消息入口或第三方分析服务。

权限：activeTab + scripting 仅手动授权的当前页；storage 保存连接配置；alarms 检查页面连接。optional_host_permissions 声明可配置 HTTP/HTTPS 服务，但只在点击开始时申请指定 LingCast 主机的权限，不主动申请所有站点。请求不携带后台 Cookie。当前后台是 HTTP，公网传输无加密；部署 HTTPS 后将插件地址切到 HTTPS 即可。

服务端按数字人绑定密钥（仅存 SHA256）、直播房间和会话版本校验；丢弃过期、重复消息，限制批量、频率和待播队列。开启欢迎/回复后仍要等待 IndexTTS/LatentSync；插件解决采集，不改变模型生成速度。手动口播优先于互动，互动优先于主持。虚拟列表、DOM 结构变更和页面暂停可能导致漏读，不能保证平台限制豁免；遇到登录验证请手动处理。

开发：`node --test extensions/chrome/tests/*.test.mjs`；DOM fixture 测试 `cd worker && uv run python -m unittest tests.test_chrome_collector`，测试使用隔离本地页面，不自动打开真实直播间。打包 `python3 extensions/package_chrome.py`，ZIP 与源码同步提交。

Chrome 官方依据：[activeTab](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab)、[扩展跨域请求](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)。
