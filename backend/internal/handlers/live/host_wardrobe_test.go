package live

import "testing"

func TestWardrobeHostHintsGatedAndSafe(t *testing.T) {
	t.Setenv("LIVE_WARDROBE_ENABLED", "")
	scripts, indices := hostLibraryForAvatar("若棠", 3)
	if len(indices) != 0 || len(scripts) != len(hostLibrary("若棠")) {
		t.Fatal("disabled hints")
	}
	t.Setenv("LIVE_WARDROBE_ENABLED", "1")
	scripts, indices = hostLibraryForAvatar("若棠", 3)
	if len(indices) != 3 || len(scripts) != len(hostLibrary("若棠"))+3 {
		t.Fatal("missing hints")
	}
	for _, i := range indices {
		if blockedLiveSpeech(scripts[i]) {
			t.Fatal("unsafe hint", scripts[i])
		}
	}
	_, indices = hostLibraryForAvatar("知夏", 2)
	if len(indices) != 0 {
		t.Fatal("wrong avatar")
	}
}
