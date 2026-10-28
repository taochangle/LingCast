package router

import (
	"github.com/gin-gonic/gin"
	"net/http/httptest"
	"strings"
	"talkingavatar/backend/internal/config"
	"testing"
)

func TestExtensionCORSOnlyCollector(t *testing.T) {
	r := Base(config.Config{GinMode: gin.TestMode, CORSOrigins: []string{"http://localhost:59480"}})
	r.POST("/api/collector/session", func(c *gin.Context) { c.Status(200) })
	r.POST("/api/admin/login", func(c *gin.Context) { c.Status(200) })
	origin := "chrome-extension://" + strings.Repeat("a", 32)
	for _, tc := range []struct {
		path, origin string
		code         int
	}{{"/api/collector/session", origin, 200}, {"/api/admin/login", origin, 403}, {"/api/collector/session", "https://evil.test", 403}, {"/api/admin/login", "http://localhost:59480", 200}} {
		req := httptest.NewRequest("POST", tc.path, nil)
		req.Header.Set("Origin", tc.origin)
		result := httptest.NewRecorder()
		r.ServeHTTP(result, req)
		if result.Code != tc.code {
			t.Fatalf("%s origin %s got %d", tc.path, tc.origin, result.Code)
		}
	}
	req := httptest.NewRequest("OPTIONS", "/api/collector/session", nil)
	req.Header.Set("Origin", origin)
	req.Header.Set("Access-Control-Request-Method", "POST")
	req.Header.Set("Access-Control-Request-Headers", "authorization,content-type")
	result := httptest.NewRecorder()
	r.ServeHTTP(result, req)
	if result.Code != 204 {
		t.Fatal("extension preflight", result.Code)
	}
}
