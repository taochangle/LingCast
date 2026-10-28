package live

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

type relayConfig struct {
	Server    string `json:"server"`
	StreamKey string `json:"streamKey"`
	Enabled   bool   `json:"enabled"`
	Revision  int64  `json:"revision"`
}

func relayKey(id uint64) string { return fmt.Sprintf("live_relay_config:%d", id) }

// Relay configuration is admin-only. Never return the saved publishing credential.
func (h *LiveHandler) Relay(c *gin.Context) {
	id, err := strconv.ParseUint(c.Param("avatarID"), 10, 32)
	if err != nil || id == 0 {
		c.JSON(400, gin.H{"error": "无效的数字人 ID"})
		return
	}
	ctx := c.Request.Context()
	var config relayConfig
	raw, err := h.q.ReadValue(ctx, relayKey(id))
	if err != nil {
		c.JSON(503, gin.H{"error": "推流配置暂时不可用"})
		return
	}
	if raw != "" {
		if json.Unmarshal([]byte(raw), &config) != nil {
			c.JSON(500, gin.H{"error": "推流配置损坏"})
			return
		}
	}
	if c.Request.Method == http.MethodPut {
		var req relayConfig
		if c.ShouldBindJSON(&req) != nil {
			c.JSON(400, gin.H{"error": "无效的推流配置"})
			return
		}
		req.Server = strings.TrimRight(strings.TrimSpace(req.Server), "/")
		u, e := url.Parse(req.Server)
		if e != nil || (u.Scheme != "rtmp" && u.Scheme != "rtmps") || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || strings.ContainsAny(req.Server, "\r\n\t ") {
			c.JSON(400, gin.H{"error": "请输入有效的 RTMP/RTMPS 服务器地址，串流密钥请单独填写"})
			return
		}
		if req.StreamKey == "" {
			req.StreamKey = config.StreamKey
		}
		req.StreamKey = strings.TrimSpace(req.StreamKey)
		if req.StreamKey == "" || len(req.StreamKey) > 4096 || strings.ContainsAny(req.StreamKey, "\r\n\t ") {
			c.JSON(400, gin.H{"error": "请输入有效的串流密钥"})
			return
		}
		req.Revision = time.Now().UnixNano()
		data, _ := json.Marshal(req)
		if h.q.WriteValue(ctx, relayKey(id), string(data)) != nil {
			c.JSON(503, gin.H{"error": "保存推流配置失败"})
			return
		}
		config = req
	}
	status, _ := h.q.ReadValue(ctx, fmt.Sprintf("live_relay_status:%d", id))
	var state any
	if status != "" {
		_ = json.Unmarshal([]byte(status), &state)
	}
	c.JSON(200, gin.H{"server": config.Server, "hasKey": config.StreamKey != "", "enabled": config.Enabled, "status": state})
}
