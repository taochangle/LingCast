package live

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestHostScriptsBounds(t *testing.T) {
	for _, scripts := range [][]string{nil, {"太短"}, {strings.Repeat("长", 71)}, make([]string, 13)} {
		if validateHostScripts(scripts) == nil {
			t.Fatalf("invalid scripts accepted: %v", scripts)
		}
	}
	if err := validateHostScripts([]string{"第一段讲完整的一句话。", "第二段自然接着介绍。"}); err != nil {
		t.Fatal(err)
	}
}
func TestHostPlanRejectsBadModelOutput(t *testing.T) {
	for _, output := range []string{`["第一段讲完整的一句话。","第二段自然接着介绍。"]`, `["太短"]`, `{"scripts":["wrong shape"]}`} {
		model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]string{"content": output}, "finish_reason": "stop"}}})
		}))
		handler := LiveHandler{openAIBaseURL: model.URL, openAIModel: "qwen35"}
		router := gin.New()
		router.POST("/plan", handler.HostPlan)
		recorder := httptest.NewRecorder()
		request := httptest.NewRequest("POST", "/plan", bytes.NewBufferString(`{"topic":"开源项目介绍","material":"这是一个 Go 项目。"}`))
		request.Header.Set("Content-Type", "application/json")
		router.ServeHTTP(recorder, request)
		expected := 502
		if strings.Contains(output, "第一段") {
			expected = 200
		}
		if recorder.Code != expected {
			t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
		}
		model.Close()
	}
}
