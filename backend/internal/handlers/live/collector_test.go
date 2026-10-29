package live

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/mysql"
	"gorm.io/gorm"
	"talkingavatar/backend/internal/models"
	"talkingavatar/backend/internal/queue"
)

func TestCollectorValidation(t *testing.T) {
	room, err := canonicalCollectorRoom("https://xiaohongshu.com/livestream/123?xsec_token=private")
	if err != nil || room != "https://www.xiaohongshu.com/livestream/123" {
		t.Fatal(room, err)
	}
	for _, url := range []string{"https://xhslink.com/o/123", "http://www.xiaohongshu.com/livestream/123", "https://www.xiaohongshu.com/explore/1"} {
		if _, err := canonicalCollectorRoom(url); err == nil {
			t.Fatal(url)
		}
	}
	event := collectorEvent{ID: "test", Kind: "message", Nickname: "小雨", Text: "你好", Observed: 100000}
	if !validCollectorEvent(event, 100001) {
		t.Fatal("valid event rejected")
	}
	if validCollectorEvent(event, 140000) || validCollectorEvent(event, 90000) {
		t.Fatal("stale/future event accepted")
	}
	event.Kind = "gift"
	if validCollectorEvent(event, 100001) {
		t.Fatal("gift accepted")
	}
}
func TestCollectorIntegration(t *testing.T) {
	dsn, redis := os.Getenv("COLLECTOR_TEST_MYSQL_DSN"), os.Getenv("REDIS_TEST_ADDR")
	if dsn == "" || redis == "" {
		t.Skip("isolated MySQL and Redis required")
	}
	db, err := gorm.Open(mysql.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err = db.AutoMigrate(&models.Avatar{}, &models.LiveSession{}, &models.LiveMessage{}); err != nil {
		t.Fatal(err)
	}
	avatar := models.Avatar{Name: "采集测试", Status: "ready"}
	if db.Create(&avatar).Error != nil {
		t.Fatal("avatar")
	}
	defer db.Delete(&avatar)
	q := queue.New(redis, "", 0, "")
	h := LiveHandler{db: db, q: q, liveControlQueueKey: "collector-test-control"}
	router := gin.New()
	router.POST("/pair/:avatarID", h.CollectorPair)
	router.DELETE("/pair/:avatarID", h.CollectorPair)
	router.POST("/session", h.CollectorSession)
	router.POST("/events", h.CollectorEvents)
	router.POST("/stop/:avatarID", h.Stop)
	request := func(method, path, token string, body any) (int, map[string]any) {
		payload, _ := json.Marshal(body)
		req := httptest.NewRequest(method, path, bytes.NewReader(payload))
		req.Header.Set("Content-Type", "application/json")
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		response := httptest.NewRecorder()
		router.ServeHTTP(response, req)
		var result map[string]any
		json.Unmarshal(response.Body.Bytes(), &result)
		return response.Code, result
	}
	if code, _ := request("POST", "/session", "", nil); code != 401 {
		t.Fatal(code)
	}
	code, pair := request("POST", fmt.Sprintf("/pair/%d", avatar.ID), "", nil)
	if code != 200 {
		t.Fatal(code, pair)
	}
	token := pair["token"].(string)
	digest, _ := q.ReadValue(t.Context(), collectorTokenKey(uint64(avatar.ID)))
	if digest == token || digest != tokenDigest(token) {
		t.Fatal("raw token persisted")
	}
	_, session := request("POST", "/session", token, map[string]any{"action": "start", "url": "https://www.xiaohongshu.com/livestream/123?xsec_token=private"})
	revision := int64(session["revision"].(float64))
	data := map[string]any{"url": "https://www.xiaohongshu.com/livestream/123", "revision": revision, "events": []collectorEvent{{ID: "first", Kind: "message", Nickname: "小雨", Text: "你好", Observed: time.Now().UnixMilli()}}}
	code, result := request("POST", "/events", token, data)
	if code != 200 || result["accepted"] != float64(1) {
		t.Fatal(code, result)
	}
	q.DeleteKey(t.Context(), fmt.Sprintf("collector_batch:%d", avatar.ID))
	code, result = request("POST", "/events", token, data)
	if code != 200 || result["accepted"] != float64(0) {
		t.Fatal("duplicate accepted", code, result)
	}
	n, _ := q.ListLen(t.Context(), fmt.Sprintf("live_welcome_queue:%d", avatar.ID))
	if n != 0 {
		t.Fatal("listen-only spoke")
	}
	data["revision"] = revision - 1
	q.DeleteKey(t.Context(), fmt.Sprintf("collector_batch:%d", avatar.ID))
	if code, _ := request("POST", "/events", token, data); code != 409 {
		t.Fatal("old room accepted", code)
	}
	// A real live session may welcome, but only once in the cooldown window.
	h.streamingSpeech = true
	live := models.LiveSession{AvatarID: avatar.ID, StreamID: "collector-test", Status: "idle"}
	db.Create(&live)
	defer db.Delete(&live)
	_, session = request("POST", "/session", token, map[string]any{"action": "start", "url": data["url"], "welcome": true})
	data["revision"] = session["revision"]
	for i := 0; i < 2; i++ {
		data["events"] = []collectorEvent{{ID: fmt.Sprint(i), Kind: "enter", Nickname: fmt.Sprint("访客", i), Observed: time.Now().UnixMilli()}}
		q.DeleteKey(t.Context(), fmt.Sprintf("collector_batch:%d", avatar.ID))
		if code, result := request("POST", "/events", token, data); code != 200 {
			t.Fatal(code, result)
		}
	}
	n, _ = q.ListLen(t.Context(), fmt.Sprintf("live_welcome_queue:%d", avatar.ID))
	if n != 1 {
		t.Fatal("welcome cooldown", n)
	}
	// Streamed speech accepts a new arrival after two seconds, rather than a minute.
	time.Sleep(2100 * time.Millisecond)
	data["events"] = []collectorEvent{{ID: "after-cooldown", Kind: "enter", Nickname: "新访客", Observed: time.Now().UnixMilli()}}
	q.DeleteKey(t.Context(), fmt.Sprintf("collector_batch:%d", avatar.ID))
	if code, result := request("POST", "/events", token, data); code != 200 {
		t.Fatal(code, result)
	}
	n, _ = q.ListLen(t.Context(), fmt.Sprintf("live_welcome_queue:%d", avatar.ID))
	if n != 2 {
		t.Fatal("streamed welcome must resume after two seconds", n)
	}

	// Closing a broadcast clears its UI/events and both interaction queues, but not the pairing.
	replyKey := fmt.Sprintf("live_external_queue:%d", avatar.ID)
	q.PushBounded(t.Context(), replyKey, "queued reply", 3)
	if code, result := request("POST", fmt.Sprintf("/stop/%d", avatar.ID), "", nil); code != 200 {
		t.Fatal(code, result)
	}
	config, err := h.readXHS(t.Context(), uint64(avatar.ID))
	if err != nil || config.Enabled || config.Revision <= int64(data["revision"].(float64)) {
		t.Fatal("collector not invalidated", config, err)
	}
	for _, key := range []string{replyKey, fmt.Sprintf("live_welcome_queue:%d", avatar.ID), fmt.Sprintf("live_xhs_events:%d", avatar.ID)} {
		n, _ := q.ListLen(t.Context(), key)
		if n != 0 {
			t.Fatal("stop kept broadcast activity", key, n)
		}
	}
	if saved, _ := q.ReadValue(t.Context(), collectorTokenKey(uint64(avatar.ID))); saved != digest {
		t.Fatal("stop changed pairing")
	}
	q.DeleteKey(t.Context(), fmt.Sprintf("collector_batch:%d", avatar.ID))
	if code, _ := request("POST", "/events", token, data); code != 409 {
		t.Fatal("stopped collector repopulated events", code)
	}
	if code, _ := request("POST", fmt.Sprintf("/stop/%d", avatar.ID), "", nil); code != 200 {
		t.Fatal("stop must clean already closed broadcast", code)
	}
	request("DELETE", fmt.Sprintf("/pair/%d", avatar.ID), "", nil)
	if code, _ := request("POST", "/session", token, map[string]any{"action": "start", "url": data["url"]}); code != 401 {
		t.Fatal("revoked key accepted")
	}
	n, _ = q.ListLen(t.Context(), fmt.Sprintf("live_welcome_queue:%d", avatar.ID))
	if n != 0 {
		t.Fatal("revoke kept queued welcome")
	}
}
