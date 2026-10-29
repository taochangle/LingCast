package live

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/gin-gonic/gin"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"talkingavatar/backend/internal/models"
	"time"
)

type xhsConfig struct {
	Client   string `json:"client,omitempty"`
	URL      string `json:"url"`
	Enabled  bool   `json:"enabled"`
	Welcome  bool   `json:"welcome"`
	Reply    bool   `json:"reply"`
	Thanks   bool   `json:"thanks"`
	Revision int64  `json:"revision"`
}

func xhsKey(id uint64) string { return fmt.Sprintf("live_xhs_config:%d", id) }

var sharedURL = regexp.MustCompile(`https://[^\s<>"“”]+`)
var xhsRoomPath = regexp.MustCompile(`^/livestream/(?:dynpath[A-Za-z0-9_-]+/)?([0-9]+)/?$`)

func parseXHSURL(raw string) (string, error) {
	if strings.TrimSpace(raw) == "" {
		return "", nil
	}
	if len(raw) > 8192 {
		return "", fmt.Errorf("分享内容过长")
	}
	value := strings.TrimRight(sharedURL.FindString(raw), "。，！、）)]")
	u, err := url.Parse(value)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Port() != "" || u.Fragment != "" {
		return "", fmt.Errorf("请粘贴小红书直播间 https 链接或完整分享文案")
	}
	switch u.Host {
	case "www.xiaohongshu.com", "xiaohongshu.com":
		if !xhsRoomPath.MatchString(u.Path) {
			return "", fmt.Errorf("需要直播间链接，不能使用笔记或个人主页链接")
		}
	case "xhslink.com":
		if u.Path == "" || u.Path == "/" {
			return "", fmt.Errorf("小红书短链接不完整")
		}
	default:
		return "", fmt.Errorf("只支持小红书直播间链接或 xhslink.com 分享短链接")
	}
	return u.String(), nil
}
func (h *LiveHandler) readXHS(ctx context.Context, id uint64) (xhsConfig, error) {
	var config xhsConfig
	raw, err := h.q.ReadValue(ctx, xhsKey(id))
	if err != nil {
		return config, err
	}
	if raw != "" {
		err = json.Unmarshal([]byte(raw), &config)
	}
	return config, err
}
func (h *LiveHandler) XHS(c *gin.Context) {
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
	config, err := h.readXHS(c.Request.Context(), id)
	if err != nil {
		c.JSON(503, gin.H{"error": "弹幕监听配置读取失败"})
		return
	}
	if c.Request.Method == http.MethodPut {
		var req xhsConfig
		if c.ShouldBindJSON(&req) != nil {
			c.JSON(400, gin.H{"error": "监听配置无效"})
			return
		}
		req.URL, err = parseXHSURL(req.URL)
		if err != nil {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}
		if req.Enabled && req.URL == "" {
			c.JSON(400, gin.H{"error": "请先填写本场直播间链接"})
			return
		}
		req.Revision = time.Now().UnixMilli()
		data, _ := json.Marshal(req)
		if h.q.WriteValue(c.Request.Context(), xhsKey(id), string(data)) != nil {
			c.JSON(503, gin.H{"error": "监听设置保存失败"})
			return
		}
		config = req
		// Drop replies from the old room; manual narration lives in another queue.
		_ = h.q.DeleteKey(c.Request.Context(), fmt.Sprintf("live_external_queue:%d", id))
		_ = h.q.DeleteKey(c.Request.Context(), fmt.Sprintf("live_welcome_queue:%d", id))
		_ = h.q.DeleteKey(c.Request.Context(), fmt.Sprintf("live_gift_queue:%d", id))
		_ = h.q.DeleteKey(c.Request.Context(), fmt.Sprintf("live_like_queue:%d", id))
	}
	raw, _ := h.q.ReadValue(c.Request.Context(), fmt.Sprintf("live_xhs_status:%d", id))
	var status map[string]any
	if json.Unmarshal([]byte(raw), &status) != nil || status["revision"] != float64(config.Revision) {
		status = nil
	}
	values, _ := h.q.ListRange(c.Request.Context(), fmt.Sprintf("live_xhs_events:%d", id), 0, 39)
	events := make([]map[string]any, 0)
	for _, value := range values {
		var event map[string]any
		if json.Unmarshal([]byte(value), &event) == nil && event["revision"] == float64(config.Revision) {
			events = append(events, event)
		}
	}
	c.JSON(200, gin.H{"config": config, "status": status, "events": events})
}

type xhsReplyScope struct{ Revision int64 }

func (h *LiveHandler) xhsReplyAllowed(ctx context.Context, avatarID uint, scope *xhsReplyScope) bool {
	if scope == nil {
		return true
	}
	config, err := h.readXHS(ctx, uint64(avatarID))
	return err == nil && config.Enabled && config.Reply && config.Revision == scope.Revision
}

// stopXHS clears per-broadcast activity while preserving pairing and room settings.
func (h *LiveHandler) stopXHS(ctx context.Context, id uint64) error {
	config, err := h.readXHS(ctx, id)
	if err != nil {
		return err
	}
	previous := config.Revision
	config.Enabled = false
	config.Revision = time.Now().UnixMilli()
	if config.Revision <= previous {
		config.Revision = previous + 1
	}
	if err := h.saveCollectorConfig(ctx, id, config); err != nil {
		return err
	}
	for _, key := range []string{
		fmt.Sprintf("live_xhs_events:%d", id),
		fmt.Sprintf("live_xhs_status:%d", id),
		fmt.Sprintf("live_external_queue:%d", id),
		fmt.Sprintf("live_welcome_queue:%d", id),
		fmt.Sprintf("live_gift_queue:%d", id),
		fmt.Sprintf("live_like_queue:%d", id),
		fmt.Sprintf("collector_received:%d:%d", id, previous),
	} {
		if err := h.q.DeleteKey(ctx, key); err != nil {
			return err
		}
	}
	return nil
}
