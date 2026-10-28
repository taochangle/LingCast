package live

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// chatCompletion supports local OpenAI-compatible servers, including Qwen.
// Reasoning is disabled and never forwarded to speech synthesis.
func (h *LiveHandler) chatCompletion(ctx context.Context, system, user string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	return h.completionWithBudget(ctx, system+"\nReply in one concise sentence: at most 35 Chinese characters or 25 English words, in the requested language. Do not include reasoning.", user, 160)
}

func (h *LiveHandler) completionWithBudget(ctx context.Context, system, user string, maxTokens int) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	payload := map[string]any{
		"model": h.openAIModel, "stream": false, "max_tokens": maxTokens,
		"chat_template_kwargs": map[string]bool{"enable_thinking": false},
		"messages":             []map[string]string{{"role": "system", "content": system}, {"role": "user", "content": user}},
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		strings.TrimRight(h.openAIBaseURL, "/")+"/chat/completions", bytes.NewReader(data))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	if h.openAIAPIKey != "" {
		req.Header.Set("Authorization", "Bearer "+h.openAIAPIKey)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("LLM HTTP status %d", resp.StatusCode)
	}
	var result struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
			FinishReason string `json:"finish_reason"`
		} `json:"choices"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&result); err != nil {
		return "", err
	}
	if len(result.Choices) == 0 {
		return "", fmt.Errorf("LLM returned no choices")
	}
	choice := result.Choices[0]
	text := strings.TrimSpace(choice.Message.Content)
	lower := strings.ToLower(text)
	if choice.FinishReason != "stop" || text == "" || strings.Contains(lower, "<think") || strings.Contains(lower, "</think>") || strings.Contains(lower, "thinking process:") {
		return "", fmt.Errorf("LLM returned empty, incomplete or reasoning output; speech skipped")
	}
	return text, nil
}
