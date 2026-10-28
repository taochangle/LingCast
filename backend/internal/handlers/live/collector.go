package live

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/gin-gonic/gin"
	"talkingavatar/backend/internal/models"
)

func collectorTokenKey(id uint64) string { return fmt.Sprintf("live_collector_token:%d", id) }
func tokenDigest(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

// CollectorPair grants only collector access to this avatar. Rotation revokes old keys.
func (h *LiveHandler) CollectorPair(c *gin.Context) {
	id, err := strconv.ParseUint(c.Param("avatarID"), 10, 32)
	if err != nil || id == 0 {
		c.JSON(400, gin.H{"error": "数字人 ID 无效"})
		return
	}
	var avatar models.Avatar
	if h.db.First(&avatar, id).Error != nil {
		c.JSON(404, gin.H{"error": "数字人不存在"})
		return
	}
	if c.Request.Method == http.MethodDelete {
		if h.q.DeleteKey(c.Request.Context(), collectorTokenKey(id)) != nil {
			c.JSON(503, gin.H{"error": "撤销失败"})
			return
		}
		config, _ := h.readXHS(c.Request.Context(), id)
		if config.Client == "extension" {
			config.Enabled = false
			if h.saveCollectorConfig(c.Request.Context(), id, config) != nil {
				c.JSON(503, gin.H{"error": "停止采集失败，请重试"})
				return
			}
			_ = h.q.DeleteKey(c.Request.Context(), fmt.Sprintf("live_external_queue:%d", id))
		}
		c.JSON(200, gin.H{"revoked": true})
		return
	}
	bytes := make([]byte, 32)
	if _, err := rand.Read(bytes); err != nil {
		c.JSON(500, gin.H{"error": "密钥生成失败"})
		return
	}
	token := fmt.Sprintf("lc_%d_%s", id, hex.EncodeToString(bytes))
	if h.q.WriteValueTTL(c.Request.Context(), collectorTokenKey(id), tokenDigest(token), 12*time.Hour) != nil {
		c.JSON(503, gin.H{"error": "密钥保存失败"})
		return
	}
	config, readErr := h.readXHS(c.Request.Context(), id)
	if readErr != nil {
		c.JSON(503, gin.H{"error": "停止旧会话失败，请重试"})
		return
	}
	if config.Client == "extension" {
		config.Enabled = false
		config.Revision++
		if h.saveCollectorConfig(c.Request.Context(), id, config) != nil {
			c.JSON(503, gin.H{"error": "停止旧会话失败，请重试"})
			return
		}
		_ = h.q.DeleteKey(c.Request.Context(), fmt.Sprintf("live_external_queue:%d", id))
	}
	c.JSON(200, gin.H{"token": token, "avatar_id": id, "avatar_name": avatar.Name, "expires_ms": time.Now().Add(12 * time.Hour).UnixMilli()})
}
func (h *LiveHandler) collectorIdentity(c *gin.Context) (uint64, bool) {
	token := strings.TrimPrefix(c.GetHeader("Authorization"), "Bearer ")
	parts := strings.Split(token, "_")
	if len(parts) != 3 || parts[0] != "lc" || len(parts[2]) != 64 {
		c.JSON(401, gin.H{"error": "采集密钥无效或已过期"})
		return 0, false
	}
	id, err := strconv.ParseUint(parts[1], 10, 32)
	if err != nil || id == 0 {
		c.JSON(401, gin.H{"error": "采集密钥无效"})
		return 0, false
	}
	digest, err := h.q.ReadValue(c.Request.Context(), collectorTokenKey(id))
	if err != nil {
		c.JSON(503, gin.H{"error": "采集认证暂不可用"})
		return 0, false
	}
	if digest == "" || subtle.ConstantTimeCompare([]byte(digest), []byte(tokenDigest(token))) != 1 {
		c.JSON(401, gin.H{"error": "采集密钥无效或已过期"})
		return 0, false
	}
	return id, true
}
func canonicalCollectorRoom(raw string) (string, error) {
	parsed, err := parseXHSURL(raw)
	if err != nil || parsed == "" {
		return "", fmt.Errorf("请在小红书直播间页面开始采集")
	}
	u, _ := url.Parse(parsed)
	if (u.Host != "www.xiaohongshu.com" && u.Host != "xiaohongshu.com") || !xhsRoomPath.MatchString(u.Path) {
		return "", fmt.Errorf("请先打开分享链接跳转后的直播页面")
	}
	return "https://www.xiaohongshu.com" + strings.TrimRight(u.Path, "/"), nil
}
func (h *LiveHandler) saveCollectorConfig(ctx context.Context, id uint64, config xhsConfig) error {
	data, _ := json.Marshal(config)
	return h.q.WriteValue(ctx, xhsKey(id), string(data))
}
func (h *LiveHandler) CollectorSession(c *gin.Context) {
	id, ok := h.collectorIdentity(c)
	if !ok {
		return
	}
	var req struct {
		Action   string `json:"action"`
		URL      string `json:"url"`
		Revision int64  `json:"revision"`
		Welcome  bool   `json:"welcome"`
		Reply    bool   `json:"reply"`
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 8192)
	if c.ShouldBindJSON(&req) != nil {
		c.JSON(400, gin.H{"error": "采集参数无效"})
		return
	}
	config, err := h.readXHS(c.Request.Context(), id)
	if err != nil {
		c.JSON(503, gin.H{"error": "配置读取失败"})
		return
	}
	switch req.Action {
	case "start":
		room, err := canonicalCollectorRoom(req.URL)
		if err != nil {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}
		revision := time.Now().UnixMilli()
		if revision <= config.Revision {
			revision = config.Revision + 1
		}
		config = xhsConfig{URL: room, Enabled: true, Welcome: req.Welcome, Reply: req.Reply, Revision: revision, Client: "extension"}
	case "stop":
		if config.Client != "extension" || config.Revision != req.Revision {
			c.JSON(409, gin.H{"error": "采集会话已更换"})
			return
		}
		config.Enabled = false
	default:
		c.JSON(400, gin.H{"error": "采集操作无效"})
		return
	}
	if h.saveCollectorConfig(c.Request.Context(), id, config) != nil {
		c.JSON(503, gin.H{"error": "配置保存失败"})
		return
	}
	_ = h.q.DeleteKey(c.Request.Context(), fmt.Sprintf("live_external_queue:%d", id))
	state := "listening"
	if !config.Enabled {
		state = "stopped"
	}
	h.collectorStatus(c.Request.Context(), id, config.Revision, state, 0)
	c.JSON(200, gin.H{"avatar_id": id, "revision": config.Revision, "room_url": config.URL, "server_ms": time.Now().UnixMilli()})
}

type collectorEvent struct {
	ID       string `json:"id"`
	Kind     string `json:"kind"`
	Nickname string `json:"nickname"`
	Text     string `json:"text"`
	Observed int64  `json:"observed_ms"`
}

func validCollectorEvent(e collectorEvent, now int64) bool {
	return len(e.ID) > 0 && len(e.ID) <= 100 && (e.Kind == "enter" || e.Kind == "message") &&
		utf8.RuneCountInString(e.Nickname) > 0 && utf8.RuneCountInString(e.Nickname) <= 40 &&
		utf8.RuneCountInString(e.Text) <= 300 && (e.Kind != "message" || strings.TrimSpace(e.Text) != "") &&
		now-e.Observed >= -5000 && now-e.Observed <= 30000
}
func (h *LiveHandler) collectorStatus(ctx context.Context, id uint64, revision int64, state string, count int64) {
	data, _ := json.Marshal(gin.H{"revision": revision, "state": state, "updated_ms": time.Now().UnixMilli(), "received": count, "browser_mode": "extension"})
	_ = h.q.WriteValueTTL(ctx, fmt.Sprintf("live_xhs_status:%d", id), string(data), 90*time.Second)
}
func (h *LiveHandler) CollectorEvents(c *gin.Context) {
	id, ok := h.collectorIdentity(c)
	if !ok {
		return
	}
	ctx := c.Request.Context()
	allowed, err := h.q.Claim(ctx, fmt.Sprintf("collector_batch:%d", id), 400*time.Millisecond)
	if err != nil {
		c.JSON(503, gin.H{"error": "接收暂不可用"})
		return
	}
	if !allowed {
		c.JSON(429, gin.H{"error": "上报过快，请稍后重试"})
		return
	}
	var req struct {
		Revision int64            `json:"revision"`
		URL      string           `json:"url"`
		Events   []collectorEvent `json:"events"`
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 32768)
	if c.ShouldBindJSON(&req) != nil || len(req.Events) > 20 {
		c.JSON(400, gin.H{"error": "每批最多20条消息"})
		return
	}
	config, err := h.readXHS(ctx, id)
	if err != nil {
		c.JSON(503, gin.H{"error": "配置读取失败"})
		return
	}
	room, err := canonicalCollectorRoom(req.URL)
	if err != nil || room != config.URL || config.Revision != req.Revision || !config.Enabled || config.Client != "extension" {
		c.JSON(409, gin.H{"error": "直播间已更换或采集已停止，请重新开始"})
		return
	}
	now := time.Now().UnixMilli()
	for _, e := range req.Events {
		if !validCollectorEvent(e, now) {
			c.JSON(400, gin.H{"error": "消息格式无效或已过期"})
			return
		}
	}
	var avatar models.Avatar
	if h.db.First(&avatar, id).Error != nil {
		c.JSON(404, gin.H{"error": "数字人不存在"})
		return
	}
	var liveCount int64
	h.db.Model(&models.LiveSession{}).Where("avatar_id = ?", id).Count(&liveCount)
	accepted := 0
	for _, e := range req.Events {
		fresh, claimErr := h.q.Claim(ctx, fmt.Sprintf("collector_seen:%d:%d:%s", id, req.Revision, tokenDigest(e.ID)), 10*time.Minute)
		if claimErr != nil {
			c.JSON(503, gin.H{"error": "消息接收失败"})
			return
		}
		if !fresh {
			continue
		}
		action := "display"
		cooldown := 12 * time.Second
		enabled := config.Reply
		if e.Kind == "enter" {
			cooldown = 60 * time.Second
			enabled = config.Welcome
		}
		signature := tokenDigest(e.Kind + "\x00" + e.Nickname + "\x00" + e.Text)
		unique, _ := h.q.Claim(ctx, fmt.Sprintf("collector_repeat:%d:%s", id, signature), map[bool]time.Duration{true: 5 * time.Minute, false: 15 * time.Second}[e.Kind == "enter"])
		queued, _ := h.q.ListLen(ctx, fmt.Sprintf("live_external_queue:%d", id))
		if unique && enabled && liveCount > 0 && queued < 3 {
			ready, _ := h.q.Claim(ctx, fmt.Sprintf("collector_cooldown:%d:%s", id, e.Kind), cooldown)
			if ready {
				if e.Kind == "enter" {
					name := strings.Map(func(r rune) rune {
						if unicode.IsControl(r) || strings.ContainsRune("<>[]{}", r) {
							return -1
						}
						return r
					}, e.Nickname)
					runes := []rune(name)
					if len(runes) > 24 {
						name = string(runes[:24])
					}
					job := gin.H{"text": "欢迎" + name + "来到直播间。", "narration": true, "job_id": fmt.Sprintf("ext-%d", time.Now().UnixNano()), "ts": now, "submitted_ms": now, "xhs_revision": req.Revision}
					data, _ := json.Marshal(job)
					if h.q.PushBounded(ctx, fmt.Sprintf("live_external_queue:%d", id), string(data), 3) == nil {
						action = "welcome"
					}
				} else {
					action = "reply"
					h.db.Create(&models.LiveMessage{AvatarID: avatar.ID, Username: e.Nickname, Role: "user", Content: e.Text})
					replyCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
					go func(e collectorEvent, revision int64) {
						defer cancel()
						h.processChatReply(replyCtx, "zh", "直播间观众「"+e.Nickname+"」说："+e.Text, avatar, 0, &xhsReplyScope{Revision: revision})
					}(e, req.Revision)
				}
			}
		}
		data, _ := json.Marshal(gin.H{"id": e.ID, "kind": e.Kind, "nickname": e.Nickname, "text": e.Text, "observed_ms": e.Observed, "action": action, "revision": req.Revision})
		_ = h.q.PrependBounded(ctx, fmt.Sprintf("live_xhs_events:%d", id), string(data), 100, 24*time.Hour)
		accepted++
	}
	count, _ := h.q.IncrementTTL(ctx, fmt.Sprintf("collector_received:%d:%d", id, req.Revision), int64(accepted), 24*time.Hour)
	h.collectorStatus(ctx, id, req.Revision, "listening", count)
	c.JSON(200, gin.H{"accepted": accepted, "received": count, "server_ms": time.Now().UnixMilli()})
}
