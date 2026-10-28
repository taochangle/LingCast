package live

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

type projectTransport func(*http.Request) (*http.Response, error)

func (f projectTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestRepositoryURLBoundary(t *testing.T) {
	for _, input := range []string{"http://github.com/a/b", "https://github.com.evil/a/b", "https://github.com/a/b/tree/main", "https://user@github.com/a/b", "https://github.com/a/..", "https://127.0.0.1/a/b"} {
		if _, err := parseRepository(input); err == nil {
			t.Errorf("accepted %s", input)
		}
	}
	if repo, err := parseRepository("https://github.com/example/project.git/"); err != nil || repo != "example/project" {
		t.Fatalf("%s %v", repo, err)
	}
	if sourceRank("vendor/pkg/main.go") != 0 || sourceRank(".env") != 0 || sourceRank("README.md") <= sourceRank("src/main.go") {
		t.Fatal("incorrect source selection")
	}
}

func TestNarrationChunkOrderAndModelLimit(t *testing.T) {
	text := strings.Repeat("这是项目讲解。", 31)
	chunks := narrationChunks(text)
	var joined string
	if len(chunks) > 5 {
		t.Fatal("exceeds atomic queue capacity")
	}
	for _, raw := range chunks {
		var entry struct {
			Text string `json:"text"`
			TS   int64  `json:"ts"`
		}
		if json.Unmarshal([]byte(raw), &entry) != nil || entry.TS != 0 {
			t.Fatal("invalid narration entry")
		}
		joined += entry.Text
	}
	if joined != text {
		t.Fatal("narration order/content changed")
	}
	if len(chunks) != 1 {
		t.Fatal("full narration must use one TTS job")
	}
}

func TestReadProjectUsesPinnedSourcesAndSeparateLongPrompt(t *testing.T) {
	original := http.DefaultTransport
	defer func() { http.DefaultTransport = original }()
	http.DefaultTransport = projectTransport(func(r *http.Request) (*http.Response, error) {
		if r.URL.Host != "api.github.com" {
			return original.RoundTrip(r)
		}
		var body string
		switch r.URL.Path {
		case "/repos/example/project/commits/HEAD":
			body = `{"sha":"fixedcommit"}`
		case "/repos/example/project/git/trees/fixedcommit":
			body = `{"tree":[{"path":"README.md","sha":"readme","type":"blob","size":50},{"path":"src/main.go","sha":"code","type":"blob","size":50}]}`
		default:
			content := "package main\nfunc main() { println(\"hello\") }"
			if strings.HasSuffix(r.URL.Path, "readme") {
				content = "# Demo project\nA small Go command line app."
			}
			data, _ := json.Marshal(map[string]string{"encoding": "base64", "content": base64.StdEncoding.EncodeToString([]byte(content))})
			body = string(data)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}, nil
	})
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload struct {
			MaxTokens int `json:"max_tokens"`
			Messages  []struct {
				Content string `json:"content"`
			} `json:"messages"`
		}
		_ = json.NewDecoder(r.Body).Decode(&payload)
		if payload.MaxTokens != 900 || !strings.Contains(payload.Messages[1].Content, "func main()") || strings.Contains(payload.Messages[0].Content, "35 Chinese") {
			t.Error("project context or long-form prompt missing")
		}
		w.Write([]byte(`{"choices":[{"message":{"content":"这是一个使用 Go 编写的命令行示例项目，程序入口直接打印问候语。"},"finish_reason":"stop"}]}`))
	}))
	defer model.Close()
	h := LiveHandler{openAIBaseURL: model.URL, openAIModel: "qwen35"}
	router := gin.New()
	router.POST("/read", h.ReadProject)
	recorder := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/read", bytes.NewBufferString(`{"url":"https://github.com/example/project"}`))
	req.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(recorder, req)
	if recorder.Code != 200 {
		t.Fatalf("%d %s", recorder.Code, recorder.Body.String())
	}
	if !strings.Contains(recorder.Body.String(), "/blob/fixedcommit/src/main.go") {
		t.Fatal("sources not pinned to commit")
	}
}
