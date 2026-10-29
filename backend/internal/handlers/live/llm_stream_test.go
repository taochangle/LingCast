package live

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestQwenPublishesFirstSentenceBeforeCompletion(t *testing.T) {
	first := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request map[string]any
		_ = json.NewDecoder(r.Body).Decode(&request)
		if request["stream"] != true {
			t.Error("streaming not requested")
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprintln(w, `data: {"choices":[{"delta":{"content":"欢迎来到这里。","reasoning_content":"private"}}]}`)
		fmt.Fprintln(w)
		w.(http.Flusher).Flush()
		select {
		case <-first:
		case <-r.Context().Done():
			return
		}
		fmt.Fprintln(w, `data: {"choices":[{"delta":{"content":"坐下聊聊天。"},"finish_reason":"stop"}]}`)
		fmt.Fprintln(w)
		fmt.Fprintln(w, "data: [DONE]")
	}))
	defer server.Close()
	handler := LiveHandler{openAIBaseURL: server.URL}
	var spoken []string
	text, err := handler.streamCompletion(context.Background(), "system", "user", func(s string) error {
		spoken = append(spoken, s)
		if len(spoken) == 1 {
			close(first)
		}
		return nil
	})
	if err != nil || text != "欢迎来到这里。坐下聊聊天。" || len(spoken) != 2 {
		t.Fatalf("text=%q spoken=%v error=%v", text, spoken, err)
	}
}

func TestQwenStreamRejectsReasoningAndDropsIncompleteTail(t *testing.T) {
	for _, body := range []string{
		"data: {\"choices\":[{\"delta\":{\"content\":\"<thi\"}}]}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"nk>秘密。\"},\"finish_reason\":\"stop\"}]}\n\n",
		"data: {\"choices\":[{\"delta\":{\"content\":\"没有结束\"},\"finish_reason\":\"length\"}]}\n\n",
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, body) }))
		handler := LiveHandler{openAIBaseURL: server.URL}
		spoken := 0
		_, err := handler.streamCompletion(context.Background(), "system", "user", func(s string) error { spoken++; return nil })
		server.Close()
		if err == nil || spoken != 0 {
			t.Fatalf("unexpected speech=%d error=%v", spoken, err)
		}
	}
}
