# LingCast Chrome 采集插件

源码使用本机测试地址，不包含生产服务器或默认密钥。先用 `python3 extensions/package_chrome.py --server <私有服务器origin>` 生成本地包，再在chrome://extensions加载解压目录。构建包已被.gitignore忽略，不得提交。

从管理员后台为目标数字人「生成新采集密钥」，复制到插件一次。插件保存在本地，重新生成/撤销后旧密钥失效。默认数字人ID3，采集只读用户手动授权的直播页，默认不自动回复或欢迎。不发送平台评论，不读取Cookie、输入框或分享令牌。

权限仅activeTab/scripting/storage/alarms及打包时指定的服务器主机；不得改成所有网站权限。公开下载包只包含服务器地址，不含认证密钥。部署HTTPS应同步重新打包。
