package live

import "testing"

func TestParseXHSRoomURL(t *testing.T) {
	for _, raw := range []string{"https://www.xiaohongshu.com/livestream/123?xsec_token=keep", "主播正在直播 https://xhslink.com/o/abc 复制打开小红书", ""} {
		value, err := parseXHSURL(raw)
		if err != nil {
			t.Fatal(raw, err)
		}
		if raw != "" && value == "" {
			t.Fatal("URL missing")
		}
	}
	value, _ := parseXHSURL("https://www.xiaohongshu.com/livestream/123?xsec_token=keep&source=web")
	if value != "https://www.xiaohongshu.com/livestream/123?xsec_token=keep&source=web" {
		t.Fatal("token changed")
	}
	for _, raw := range []string{"https://www.xiaohongshu.com/explore/123", "http://www.xiaohongshu.com/livestream/123", "https://www.xiaohongshu.com.evil.test/livestream/123", "https://u@www.xiaohongshu.com/livestream/123", "https://www.xiaohongshu.com:123/livestream/123", "https://xhslink.com/", "https://127.0.0.1/"} {
		if _, err := parseXHSURL(raw); err == nil {
			t.Fatal("invalid URL accepted", raw)
		}
	}
}
