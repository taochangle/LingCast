package live

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"
)

func requestedWardrobe(text string) bool {
	switch strings.Trim(strings.TrimSpace(text), "，。！？!?~～ ") {
	case "换衣服", "换一套衣服":
		return true
	}
	return false
}

// Assets are operator-prepared; comments can request a toggle, never a path/profile.
func (h *LiveHandler) queueWardrobe(ctx context.Context, avatarID uint, revision int64) string {
	if avatarID != 3 || os.Getenv("LIVE_WARDROBE_ENABLED") != "1" {
		return "wardrobe_unavailable"
	}
	ready, err := h.q.Claim(ctx, fmt.Sprintf("live_wardrobe_cooldown:%d:%d", avatarID, revision), 2*time.Minute)
	if err != nil || !ready {
		return "display"
	}
	now := time.Now().UnixMilli()
	data, _ := json.Marshal(map[string]any{"action": "wardrobe", "xhs_revision": revision, "ts": now, "job_id": fmt.Sprintf("wardrobe-%d", time.Now().UnixNano())})
	if h.q.PushBounded(ctx, fmt.Sprintf("live_external_queue:%d", avatarID), string(data), 3) != nil {
		return "display"
	}
	return "wardrobe"
}
