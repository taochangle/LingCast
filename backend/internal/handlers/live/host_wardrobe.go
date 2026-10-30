package live

import "os"

var wardrobeHostHints = []string{
	"想看我换套穿搭，可以在评论里发“换衣服”。我会说完这一段，再去换好回来。",
	"今天也准备了不同风格的穿搭。想看看下一套，就在评论里告诉我“换衣服”吧。",
	"喜欢优雅一点，还是知性一点的穿搭？想看下一套，可以发“换衣服”，等我站定了再接着聊。",
}

func wardrobeHostEnabled(avatarID uint64) bool {
	return avatarID == 3 && os.Getenv("LIVE_WARDROBE_ENABLED") == "1"
}

func hostLibraryForAvatar(name string, avatarID uint64) ([]string, []int) {
	scripts := hostLibrary(name)
	if !wardrobeHostEnabled(avatarID) {
		return scripts, nil
	}
	indices := make([]int, 0, len(wardrobeHostHints))
	for _, text := range wardrobeHostHints {
		indices = append(indices, len(scripts))
		scripts = append(scripts, text)
	}
	return scripts, indices
}
