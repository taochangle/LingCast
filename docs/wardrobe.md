# 沈若棠换装与自动主持提醒

## 功能与播放顺序

只对沈若棠ID3启用。评论精确发送「换衣服」或「换一套衣服」，按白衣→酒红裙→香槟套装→白衣轮换；同一场次两分钟换装冷却。蓝衣3001保留为旧版独立素材，不在当前轮换中。观众不能传人物、素材路径或模型参数。

当前口播彻底结束 → 旧造型完整说完「好呀，我去换件衣服，等我一下。」 → 闭嘴离场 → 空镜 → 新造型闭嘴入场并站定 → writer同时提交新idle和新口型renderer，先显示至少一帧新idle → 新造型说「我回来啦，这套怎么样？」 → 回场语播放完才恢复互动和主持。不会说话途中离开，也不会走路时说话。

每个方向使用独立intro（旧衣、有声音）、motion（无音轨）、outro（新衣、有声音），三段总长不超过20秒。先下载、检查SHA256/尺寸/帧率/音轨并完整解码，再开始告别；运动阶段注入零PCM。FIFO和播放屏障保证阶段顺序。等待后续帧时保持运动最后一帧，避免闪回旧衣。新造型后续任意口播也使用对应renderer，不仅更换待机。

手动口播和已有互动遵守原队列优先级；正在执行的短换装不被评论打断。待执行请求受房间revision和两分钟过期限制。新开播/重建直播默认恢复白衣，不改变人物全局默认场景。

## 主持提示

ID3「主持话术库」额外包含三条固定换装提醒，不改变用户自写的有限讲稿。首次至少4分钟且讲完3段普通主持；之后每4分钟至多一条，至少隔3段普通主持，三条轮换。手动口播/互动优先；暂停、结束时不播。

例如：「想看我换套穿搭，可以在评论里发‘换衣服’。我会说完这一段，再去换好回来。」提醒本身不自动换装。仅API开关、完整素材清单、所有口型ready身份与素材哈希，以及插件采集/自动回复均就绪时提示。旧主持任务需重新启动「主持话术库」以加入提醒。

## 常驻模型与发布边界

原CosyVoice59495、MuseTalk59496保持运行，白衣3和知夏2仍走59496，保留原缓存。旧进程没有本轮形象注册代码，不能通过仅上传JSON假装支持新衣服，也不做进程注入。

新增`deploy/lingcast-wardrobe.service`，仅监听127.0.0.1:59497。它单独加载一份MuseTalk权重，使用GPU2余量和已离线预处理的人脸；这是新增副本，不是原模型热更新或重启。3002/3003分别表示酒红/香槟，仍是同一人物和原音色。Worker设置`MUSETALK_WARDROBE_URL`后仅新造型路由到59497；ready快照仅合并预留造型ID。新服务不可达时不宣传换装，原白衣仍可用。

隔离数据目录：`/data1/taoxin/lingcast-wardrobe-local-test/20261030`。模型文件只读复用，形象缓存为已离线完成的真实新衣素材，不允许复制白衣缓存冒充新衣。`prepared-looks.json`包含profile、base_key和input_sha256；输入视频及预处理目录必须与哈希一致，形象注册时不重新加载权重、不进行人脸预处理。

仅构建/更新api-admin、api-live、frontend-admin并重启原生lingcast-stream。**不要运行会重启原模型的全量deploy/chengdu.sh。** 私有配置与旧单元先备份，核对原模型PID和无关容器启动时间未变，不新增公网端口。

## 配置与素材准备

仓库`deploy/wardrobe.json`是默认关闭的示例；运行清单放`runtime/wardrobe.json`。Worker的`WARDROBE_MANIFEST`指向完整运行清单，API的`LIVE_WARDROBE_ENABLED=1`为另一道开关。不要部署本地预览的本地文件键清单。

正式输入来自`/Users/taoxin/my-videos/assets/presenters/ruotang-wardrobe-style-v2/`，三套各有portrait/anchor/idle/exit/enter，共用empty-loop。576×1024、25fps、H.264/yuv420p，走位和idle无音轨。v1蓝衣素材保留。服装褶皱和动作存在生成视频的一致性局限，技术检查不等于完美画质或人工听审。

`worker/prepare_wardrobe_profile.py --look burgundy|champagne --source /absolute/idle.mp4 --gpu <空闲GPU>`用于离线准备，拒绝覆盖旧输入，要求至少12GB空闲显存。它加载独立模型副本，不操作现有模型，输出候选描述而不自动安装。准备前确认GPU实际余量及其他任务。

`worker/build_wardrobe_show.py --assets /absolute/assets --output /absolute/build --looks-file /absolute/looks.json --upload`调用常驻配音/口型生成三方向分离口播与静音走位，检查总长及身份；上传只创建对应对象，不自动启用。上传后再审核运行manifest并开双开关。

## 验证与回退

Python覆盖阶段屏障、运动零PCM、新idle先于回场语、预处理身份绑定、新旧renderer隔离、失败不换装、三套轮换、主持时钟及就绪门控。CPU测试不调用GPU；本地59509是文件适配器/Watchdog预览，不能代替生产链路验证。

生产验证记录放`runtime/wardrobe-verification/report.json`及`three-look-live.mp4`，用内网SRS录制，禁止向小红书发布测试评论或擅自开启外部RTMP。测试前后恢复采集/主持/推流配置，测试事件不作为真实观众验收。四分钟提示频率由模拟时钟测试，线上只核对配置和就绪判断。

回退时关闭API开关并恢复Worker旧配置/原单元，只重启相关API和stream Worker。原模型不用重启；停止独立wardrobe服务前须先让新造型场次结束并恢复白衣。原配置备份：`runtime/backups/20261030-wardrobe/`。

### 2026-10-30 发布验证

功能代码93f120f已定向发布。真实采集接口测试请求返回wardrobe动作，随后完整运行三个方向并在每次换装后播放新句，均回idle；三轮含后续口播各耗时约23/21/21秒，这不是单独换装耗时。主持库三条提示与完整素材ready检查通过，四分钟频率仍以模拟时钟测试为准。测试为操作员合成请求，不冒充真实观众弹幕；未向小红书发送评论或推流。恢复原关播状态及采集/主持/relay配置。原模型PID保持不变，新增59497只绑定loopback。

## 后台手动换装

Live Studio的ID3开播按钮旁新增「换下一套」。管理员POST `/api/live/3/wardrobe`，不依赖插件、采集开关或自动回复。命令进入原手动队列，沿用完整播放屏障；30秒重复点击限频，排队不等于立即离场。仅已开播的ID3且API换装开关开启时可提交，不接受客户端指定文件或造型。Worker校验本次运行的stream_id、创建时间及两分钟有效期，旧场次指令不能在重新开播后执行。
