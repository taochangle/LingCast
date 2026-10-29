package live

import (
	"math/rand/v2"
	"strings"
)

// Explicit short requests only. Free-form instructions still go through guarded Qwen.
func requestedProgramme(text string) string {
	text = strings.TrimSpace(text)
	text = strings.Trim(text, "，。！？!?~～ ")
	switch text {
	case "讲个笑话", "讲一个笑话", "来个笑话", "笑话":
		return "joke"
	case "夸夸我", "夸我一下", "夸夸":
		return "praise"
	case "晚安", "说晚安", "来个晚安", "说句晚安":
		return "goodnight"
	}
	return ""
}

var programmeScripts = map[string][]string{
	"joke": {
		"小番茄问大番茄，我们去哪儿呀？大番茄说，别问啦，再走两步就到沙拉里了。",
		"我问闹钟，你每天这么准时，不累吗？它说，累啊，可是总有人按着我不让我下班。",
		"小猫去应聘，老板问你有什么特长？它说，我特别擅长，把键盘上的工作都踩一遍。",
	},
	"praise": {
		"忙了一天，还愿意给自己找一点快乐，这就很棒。今天的你，值得一句认真又大声的夸奖。",
		"你不需要每件事都做得完美。愿意尝试，愿意往前走，就已经有属于你的闪光点啦。",
		"这句夸奖送给屏幕前的你。愿你今天的小努力都被看见，也记得给自己一点掌声。",
	},
	"goodnight": {
		"今天就先到这里吧，没做完的事留给明天。把肩膀放松，好好休息，晚安，愿你睡个好觉。",
		"给今天画个小小的句号吧。把烦恼暂时放在门外，明天醒来再慢慢处理。晚安。",
		"夜深啦，记得给自己留一点安静的时间。愿你今晚睡得踏实，明天醒来有新的好心情。",
	},
}

func programmeSpeech(kind string) string {
	scripts := programmeScripts[kind]
	if len(scripts) == 0 {
		return ""
	}
	return scripts[rand.IntN(len(scripts))]
}
