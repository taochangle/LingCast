# 沈若棠 ID 3 换装小节目（本地实现，未上线）

## 第一版行为

精确评论「换衣服」「换一套衣服」按审核后的order轮换；当前新风格配置为白衣→酒红裙→香槟套装→白衣，v1浅蓝素材与别名3001保留，不接受评论提供的素材路径或人物。保持同一张脸、盘发、小花耳饰、背景、机位和当前声音。两分钟触发冷却、原评论队列有界；其他数字人不启用。关闭状态返回 `wardrobe_unavailable`，不交给 Qwen 编造换装承诺。

当前已开始的口播先完成；换装选中后等待内部预备帧和音频排空，播放期间不消费新互动或主持。演出结束继续取原队列，不清除评论。关播/换房 revision 使未开演的旧请求失效；已经开始的短表演不被评论硬打断。原有消息两分钟过期仍适用，并不保证过期评论全部补播。

严格演出顺序：当前口播彻底结束 → 旧造型完整说完「好呀，我去换件衣服，等我一下。」 → 闭嘴离场 → 空镜 → 新造型闭嘴入场并站定 → writer提交新idle及口型renderer，并显示至少一帧新idle → 新造型才说「我回来啦，这套怎么样？」 → 回场语实际播放完才恢复其他队列。无脱衣画面。

每个轮换方向输出三个分离文件：`intro.mp4`（旧造型、有音轨）、`motion.mp4`（离场+空镜+入场，**不允许有音轨**）、`outro.mp4`（新造型、有音轨），总长最多20秒。禁止把回场语烘焙到入场动画或整个过场里。builder和Worker都拒绝音轨混用，清单同时绑定两段口播的renderer和base_key。原始exit/enter仍按每套衣服分别交付。

所有素材先完成下载、校验和解码，再开始告别语；运动阶段固定注入零PCM。若播放队列暂时缺帧，保持最后一帧运动画面，不闪回旧idle；口型/idle提交后清除保持帧。FIFO保证告别语最后一帧和声音结束才离场，提交屏障保证新idle实际入流后才有回场语；末尾屏障保证其他回复不会抢在回场语结束前播放。

新待机/过场 SHA256、尺寸、帧率、音轨和新口型 renderer 先检查；全部通过才开演。闭嘴入场的最后一帧播放完，在 Watchdog 帧边界同时更换 idle 和 renderer，然后再播放回场口播，随后讲话继续用新衣服。期间禁用其他场景替换和旧造型动作 key，避免下一句变回原衣服。直播重建/重新开播第一版恢复白衣，不写入全局人物默认造型。

## 状态与启用

- `deploy/wardrobe.json` 默认 `enabled:false`，缺素材/哈希，不能播放。
- API `LIVE_WARDROBE_ENABLED=1` 是第二道开关，默认关闭；现有插件不需要新增权限或用户字段。
- Worker `WARDROBE_MANIFEST` 可指定审核后的本机 manifest；默认仓库 `deploy/wardrobe.json`。
- Redis `live_playback:3` 的 wardrobe 字段报告 `white/blue/failed`。后台事件显示「已排队换装」或「换装素材尚未就绪」。失败不提交新造型。

## 素材协作

用户已授权会话「制作 GitHub Trending 周榜视频」(`01a16268-0a72-7113-b625-1c530112ed5a`) 制作素材，任务已发出。目录：
`/Users/taoxin/my-videos/assets/presenters/ruotang-wardrobe-v1/`。

以 `/Users/taoxin/my-videos/assets/presenters/candidates-2026-10-28/H-refined-frontal/portrait.png` 为身份参考。
要求交付 white/portrait.png、white/idle.mp4、blue/portrait.png、blue/idle.mp4、两套各自的 exit.mp4/enter.mp4、empty-loop.mp4、manifest.json 和 review.md。576×1024、9:16、25fps、H.264/yuv420p、固定机位，原始动画静音。新idle闭嘴自然眨眼呼吸，6~8秒可循环。前后身份、衣服、背景及姿势衔接必须视觉审核，不以解码成功替代画质验收。

## 新造型口型

3001 是 ID3 浅蓝造型的内部 renderer 别名，不创建新人物或新声音。代码支持从 `MUSETALK_ROOT/prepared-looks.json` 安装已离线准备的形象：

```json
{"3001":{"profile":"ruotang_blue","base_key":"scene_videos/ruotang-wardrobe-v1/blue-idle.mp4","input_sha256":"<blue/idle.mp4 的 SHA256>"}}
```

视频放 ROOT/inputs/ruotang_blue.mp4，预处理目录为 ROOT/source/results/v15/avatars/bench_ruotang_blue，需包含 MuseTalk 的 latents、脸框、全帧、mask，且 avator_info.json 中 version=v15、bbox_shift=0；补写 lingcast-source.sha256 绑定准确视频。**不能只复制原白衣的预处理目录。** 人脸预处理需离线完成，不在直播请求中做。

新的服务实现只读取这些已完成数据，复用原模型对象并预热后公布3001，不重载权重；拒绝替换已注册形象或未完成的输入。该能力目前只是本地代码，线上仍运行旧服务，不能仅上传一个JSON就宣称已经支持。先在独立测试服务验证新代码和显存，再单独安排发布；本次未更新、重启线上模型。服务源码变化也可能改变原媒体缓存指纹，发布时须重新核对原90条预缓存，不应假定它们继续命中。

## 演出合成

`worker/build_wardrobe_show.py` 通过现有 CosyVoice/MuseTalk 接口获得白衣和浅蓝口播帧，拼接静音离场/空镜/回场。先确认3和3001均ready才开始，按looks-file中的order为每个相邻轮换方向输出intro/motion/outro（白衣/酒红/香槟共三个方向、九段MP4）及默认关闭的 wardrobe.json；默认仅本地输出。`--upload` 只上传指定暂存对象，不覆盖当前idle、不写线上配置、不启用开关。蓝idle需先审核，base_key必须与3001注册值一致。

```bash
cd worker
uv run python build_wardrobe_show.py --assets /absolute/ruotang-wardrobe-v1 \
  --output /absolute/wardrobe-build \
  --looks-file /absolute/prepared-wardrobe-looks.json
```

先按清单顺序预览两方向的三阶段片段、核对音色和走位，准备并检查 manifest 中全部对象后才启用。图片视频已交付本地测试版，production_ready=false。已用隔离离线口型和真实Watchdog生成两方向本地预览（含声音），尚未接生产房间或真实弹幕触发，新造型动态服装一致性仍需人工评审。

## 本地验证

- Go tests：精确触发、默认关闭、avatar限制；REDIS_TEST_ADDR 启用真实临时Redis队列及冷却测试。
- Python：`tests.test_wardrobe`、`tests.test_wardrobe_build`、`tests.test_musetalk_stream`、`tests.test_host_director`。覆盖缺素材/校验失败、新旧renderer隔离、帧边界提交、预处理形象注册不替换模型对象。
- CPU FFmpeg 合成测试：合成两方向小视频、验证尺寸/帧率/音轨/哈希，确认不上传或开启功能。它使用人工纯色帧，不代表实际人物效果。
- 前端构建通过。未部署，未重启线上服务。

离线预处理入口 `worker/prepare_wardrobe_profile.py --source /absolute/blue/idle.mp4 --gpu <明确选择的空闲GPU>`：拒绝覆盖已有蓝衣输入/预处理目录，检查视频规格与至少12GB可用显存，仅在单独离线进程加载权重；不重启或修改现有进程。输出 prepared-look-3001.candidate.json，**不自动安装到 prepared-looks.json**。12GB检查不等于GPU预留，执行前仍需确认该GPU没有其他用户排队任务。本次只检查了CLI/help和语法，没有在GPU上执行。

2026-10-30 时序修订已同步给素材会话：每套衣服独立静音exit/enter/idle，离场与入场全程闭嘴，口播由本代码另行合成。新增真实Watchdog写帧测试验证旧口播排空、静音走位、切新idle先于回场语、回场语结束屏障，以及拒绝旧版整段视频清单。

本地联调预览：http://127.0.0.1:59508/，仅loopback。实际口播素材在服务器隔离目录离线生成，没有安装3001到生产常驻服务、没有修改线上配置。白→蓝17.68秒、蓝→白17.60秒；走位零PCM、先新idle后回场语检查通过，两文件完整解码通过。复现见experiments/wardrobe_preview/README.md。

## 两套新风格均保留（本地配置更新）

用户确认酒红方领连衣裙、香槟缎面衬衫配深色半裙两套都要。素材会话已收到补齐两套idle/exit/enter的要求，不覆盖v1。默认order为white、burgundy、champagne；对应内部renderer为3、3002、3003。旧blue/3001仍可在独立旧配置中使用，不占用或被新造型覆盖。所有开关仍关闭，未更新线上。

`ai/wardrobe_profiles.py`集中固定别名映射；清单order必须完整、无重复且仅含允许造型。builder使用 `--looks-file` 读取order/looks，对每套检查idle与口型输入哈希，循环制作相邻方向；不让观众直接传profile或文件路径。准备口型时 `prepare_wardrobe_profile.py --look burgundy|champagne --source ... --gpu ...`，分别输出独立candidate描述文件，不自动安装；旧blue操作可用 `--look blue`。所有新造型沿用原声音，不训练新声音。

looks-file结构示例（idle_key需先替换为已准备renderer公布的准确对象键）：
```json
{"order":["white","burgundy","champagne"],"looks":{"white":{"renderer_id":3,"idle_key":"<当前白衣对象键>"},"burgundy":{"renderer_id":3002,"idle_key":"scene_videos/ruotang-wardrobe/burgundy-<sha>.mp4"},"champagne":{"renderer_id":3003,"idle_key":"scene_videos/ruotang-wardrobe/champagne-<sha>.mp4"}}}
```

本地24项Python测试通过，包含三套轮换及三方向真实FFmpeg合成，原严格播放边界检查保留。当前59508仍是v1白衣↔蓝衣预览；新款视频完成前不替换该页面，不声称两套新风格已可播放。

## 自主主持中的换装提示（本地实现）

ID3启动「主持话术库」时，仅在API换装开关启用的情况下加入3条固定引导，例如「想看我换套穿搭，可以在评论里发‘换衣服’。我会说完这一段，再去换好回来。」不自动触发换装，不改用户自行编写的有限分段讲稿。

提示索引单独记录，不进入普通欢迎/陪伴随机轮换。首次至少等4分钟且讲完3段正常主持，之后每4分钟至多一条、至少隔3段正常主持，3条提示轮流使用。它们使用原话术库预生成/播放缓存，不额外走Qwen。手动/评论/感谢/欢迎仍然优先，暂停和结束主持不播提示。

Worker还检查本场采集enabled/reply、整套manifest完整性，以及resident最近warmup的ready快照中每套衣服的base_key/input_sha256是否匹配；未准备齐、缺新口型、关闭自动回复均不宣传功能。ready快照来自实际warmup，不在每个tick请求或重启模型；若在开播后才安装新profile，需后续warmup刷新该快照。API开关变化后继续/新开主持会刷新启用标记；旧主持配置没有提示索引则不自动补入，重新启动话术库才加入新提示。

本次仅本地代码/测试，当前线上及本地未完成的换装配置仍关闭。没有预生成线上提示媒体或部署任何服务。

最新本地联调：127.0.0.1:59509。酒红裙交付完整，已接白衣↔酒红与主持提示联合样片；香槟仍缺正式enter.mp4，尚未接入，不视为三套验收完成。主持门控测试用模拟时钟，正常段落仅计时占位，不展示；实际提示口播为原音色+真实口型。联合样片明确模拟观众发送指令，提醒不会自动换装。未更新线上，未重启原模型/Worker；更多复现与限制见experiments/wardrobe_preview/README.md。

补充更新：正式香槟入场已收到，前文缺项记录已解决。当前59509已切到本地验证版本20261030-130322，白衣→酒红裙→香槟套装→白衣三个方向和主持提醒联动共四条成片；13项素材哈希/规格、四条解码/静音/新idle先于回场语检查通过。主持提示门控已覆盖三套造型，四分钟等待用模拟时钟测试。26项Python测试通过。local complete不代表production_ready；服装动态、实际听感与口型观感仍需人工评审。线上配置仍关闭，未部署或重启模型。

## 不重启原模型的定向发布

新增 `deploy/lingcast-wardrobe.service` 独立形象服务，仅监听127.0.0.1:59497，使用已离线准备的形象和GPU2剩余显存。它单独加载一份权重，不能描述成原进程热更新。原CosyVoice/59495和MuseTalk/59496不重启，原白衣及知夏仍走59496并保留原媒体缓存。Worker设置`MUSETALK_WARDROBE_URL=http://127.0.0.1:59497`，仅3001/3002/3003走该接口；ready快照合并经过白名单限制的造型。独立服务不可达时不宣传换装，原白衣仍可使用。

发布前核对独立服务ready与所有素材哈希、构建并上传三方向intro/motion/outro，再将完整清单安装到runtime/wardrobe.json并启用API开关。仅更新api-admin/api-live/frontend-admin和原生stream Worker，不运行会重启模型的全量chengdu.sh。私有配置、旧单元和PID记录先备份；现有模型PID和无关容器启动时间须在发布前后保持不变。此节为发布步骤，实际完成情况以runtime发布记录为准。
