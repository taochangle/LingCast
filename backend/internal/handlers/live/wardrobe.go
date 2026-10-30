package live

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/gin-gonic/gin"
	"os"
	"strconv"
	"strings"
	"talkingavatar/backend/internal/models"
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

// ChangeWardrobe is an authenticated operator command, independent of collection.
func (h *LiveHandler) ChangeWardrobe(c *gin.Context) {
	id, err := strconv.ParseUint(c.Param("avatarID"), 10, 32)
	if err != nil || id == 0 {
		c.JSON(400, gin.H{"error": "数字人 ID 无效"})
		return
	}
	if id != 3 || os.Getenv("LIVE_WARDROBE_ENABLED") != "1" {
		c.JSON(409, gin.H{"error": "当前数字人尚未启用换装"})
		return
	}
	var session models.LiveSession
	if h.db.Where("avatar_id = ?", id).First(&session).Error != nil {
		c.JSON(409, gin.H{"error": "请先打开数字人直播"})
		return
	}
	action := h.queueAdminWardrobe(c.Request.Context(), session)
	if action != "wardrobe" {
		c.JSON(429, gin.H{"error": "换装正在排队或队列繁忙，请稍后再试"})
		return
	}
	c.JSON(202, gin.H{"queued": true})
}

func (h *LiveHandler) queueAdminWardrobe(ctx context.Context, session models.LiveSession) string {
	key := fmt.Sprintf("live_wardrobe_admin_cooldown:%d", session.AvatarID)
	ready, err := h.q.Claim(ctx, key, 30*time.Second)
	if err != nil || !ready {
		return "busy"
	}
	now := time.Now().UnixMilli()
	data, _ := json.Marshal(map[string]any{"action": "wardrobe", "source": "admin", "stream_id": session.StreamID, "ts": now, "job_id": fmt.Sprintf("admin-wardrobe-%d", time.Now().UnixNano())})
	if h.q.PushBounded(ctx, fmt.Sprintf("live_queue:%d", session.AvatarID), string(data), 10) != nil {
		_ = h.q.DeleteKey(ctx, key)
		return "busy"
	}
	return "wardrobe"
}
