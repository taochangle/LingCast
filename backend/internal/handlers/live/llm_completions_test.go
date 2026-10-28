package live

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestLocalQwenCompletion(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer test-token" {
			t.Error("incorrect endpoint/auth")
		}
		var payload struct {
			Model    string          `json:"model"`
			Template map[string]bool `json:"chat_template_kwargs"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		thinking, ok := payload.Template["enable_thinking"]
		if !ok || thinking || payload.Model != "qwen35" {
			t.Error("reasoning must be explicitly disabled")
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"choices":[{"message":{"content":"你好，欢迎来到直播间！","reasoning_content":"private"},"finish_reason":"stop"}]}`))
	}))
	defer server.Close()
	h := LiveHandler{openAIAPIKey: "test-token", openAIModel: "qwen35", openAIBaseURL: server.URL + "/v1/"}
	text, err := h.chatCompletion(context.Background(), "system", "user")
	if err != nil || text != "你好，欢迎来到直播间！" {
		t.Fatalf("text=%q err=%v", text, err)
	}
}

func TestLocalQwenRejectsUnspeakableReply(t *testing.T) {
	for _, body := range []string{
		`{"choices":[{"message":{"content":"Thinking Process: secret"},"finish_reason":"stop"}]}`,
		`{"choices":[{"message":{"content":"<think>secret</think>你好"},"finish_reason":"stop"}]}`,
		`{"choices":[{"message":{"content":"没有说完"},"finish_reason":"length"}]}`,
		`{"choices":[]}`, `{"choices":[{"message":{"content":""},"finish_reason":"stop"}]}`,
	} {
		t.Run(body, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(body)) }))
			defer server.Close()
			h := LiveHandler{openAIBaseURL: server.URL}
			if text, err := h.chatCompletion(context.Background(), "system", "user"); err == nil || text != "" {
				t.Fatalf("unsafe text=%q err=%v", text, err)
			}
		})
	}
}

func TestLocalQwenHonorsCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	h := LiveHandler{openAIBaseURL: "http://127.0.0.1:1/v1"}
	start := time.Now()
	if _, err := h.chatCompletion(ctx, "system", "user"); err == nil {
		t.Fatal("expected cancellation")
	}
	if time.Since(start) > time.Second {
		t.Fatal("cancellation did not return promptly")
	}
}
