package live

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"talkingavatar/backend/internal/models"
	"time"
	"unicode/utf8"
)

// streamCompletion publishes only complete spoken sentences, never reasoning deltas.
// Already published sentences cannot be recalled if the upstream later fails.
func (h *LiveHandler) streamCompletion(ctx context.Context, system, user string, emit func(string) error) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	body, err := json.Marshal(map[string]any{
		"model": h.openAIModel, "stream": true, "max_tokens": 240,
		"chat_template_kwargs": map[string]bool{"enable_thinking": false},
		"messages":             []map[string]string{{"role": "system", "content": system + "\n用2至3个完整短句口语化回应，总计不超过80字。句末用句号。不要思考过程、标题或动作标签。"}, {"role": "user", "content": user}},
	})
	if err != nil {
		return "", err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(h.openAIBaseURL, "/")+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+h.openAIAPIKey)
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return "", fmt.Errorf("Qwen HTTP %d", response.StatusCode)
	}
	scanner := bufio.NewScanner(response.Body)
	scanner.Buffer(make([]byte, 4096), 1<<20)
	var full, pending string
	finish := ""
	published := 0
	publish := func(text string) error {
		text = strings.TrimSpace(actionTagAnywhere.ReplaceAllString(text, ""))
		if blockedLiveSpeech(text) {
			return fmt.Errorf("speech rejected by live guard")
		}
		if text == "" {
			return nil
		}
		if published >= 4 {
			return fmt.Errorf("too many reply sentences")
		}
		if err := emit(text); err != nil {
			return err
		}
		published++
		return nil
	}
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		line = strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if line == "[DONE]" {
			break
		}
		var part struct {
			Choices []struct {
				Delta struct {
					Content string `json:"content"`
				} `json:"delta"`
				Finish string `json:"finish_reason"`
			} `json:"choices"`
		}
		if err := json.Unmarshal([]byte(line), &part); err != nil {
			return full, err
		}
		if len(part.Choices) == 0 {
			continue
		}
		delta := part.Choices[0].Delta.Content
		full += delta
		lower := strings.ToLower(full)
		if blockedLiveSpeech(full) || len(full) > 4000 || strings.Contains(lower, "<think") || strings.Contains(lower, "</think>") || strings.Contains(lower, "thinking process:") {
			return "", fmt.Errorf("unspeakable Qwen output")
		}
		pending += delta
		for {
			end := -1
			for index, r := range pending {
				if strings.ContainsRune("。！？!?；;\n", r) {
					end = index + utf8.RuneLen(r)
					break
				}
			}
			if end < 0 && utf8.RuneCountInString(pending) > 60 {
				end = len(string([]rune(pending)[:60]))
			}
			if end < 0 {
				break
			}
			if err := publish(pending[:end]); err != nil {
				return full, err
			}
			pending = pending[end:]
		}
		if part.Choices[0].Finish != "" {
			finish = part.Choices[0].Finish
		}
	}
	if err := scanner.Err(); err != nil {
		return full, err
	}
	if finish != "stop" {
		return full, fmt.Errorf("incomplete Qwen stream; tail discarded")
	}
	if err := publish(pending); err != nil {
		return full, err
	}
	return full, nil
}

func (h *LiveHandler) processStreamingReply(ctx context.Context, lang, userText string, avatar models.Avatar, userID uint, scope *xhsReplyScope) (string, int) {
	if blockedLiveInput(userText) {
		log.Printf("[LIVE_GUARD] avatar=%d input blocked", avatar.ID)
		return "", 0
	}
	started := time.Now()
	programme := ""
	if scope != nil {
		programme = requestedProgramme(userText)
	}
	var facts []string
	if programme == "" {
		facts = h.retrieveKnowledge(ctx, avatar.ID, userText, 500*time.Millisecond)
	}
	system := chatSystemPrompt(avatar, lang, nil, nil)
	userContext := liveUserContext(userText, h.recentMessages(avatar.ID, 10), facts)
	spoken := []string{}
	key := liveQueueKey(avatar.ID)
	if scope != nil {
		key = fmt.Sprintf("live_external_queue:%d", avatar.ID)
	}
	emit := func(sentence string) error {
		if blockedLiveSpeech(sentence) {
			return fmt.Errorf("speech blocked")
		}
		if !h.xhsReplyAllowed(ctx, avatar.ID, scope) {
			return fmt.Errorf("room changed")
		}
		payload := map[string]any{"text": sentence, "ts": time.Now().UnixMilli(), "narration": true, "job_id": fmt.Sprintf("reply-%d-%d", avatar.ID, started.UnixNano()), "submitted_ms": started.UnixMilli()}
		if scope != nil {
			payload["xhs_revision"] = scope.Revision
		}
		data, _ := json.Marshal(payload)
		if err := h.q.PushBounded(ctx, key, string(data), 5); err != nil {
			return err
		}
		spoken = append(spoken, sentence)
		_ = h.q.RPushList(ctx, liveHistoryKey(avatar.ID), sentence)
		log.Printf("[QWEN_STREAM] avatar=%d sentence=%d queued_after_ms=%d", avatar.ID, len(spoken), time.Since(started).Milliseconds())
		return nil
	}
	var err error
	if programme != "" {
		err = emit(programmeSpeech(programme))
	} else {
		_, err = h.streamCompletion(ctx, system, userContext, emit)
	}
	if err != nil {
		log.Printf("[QWEN_STREAM] avatar=%d stopped: %v", avatar.ID, err)
	}
	reply := strings.Join(spoken, "")
	if reply != "" && h.xhsReplyAllowed(ctx, avatar.ID, scope) {
		ragJSON := ""
		if len(facts) > 0 {
			data, _ := json.Marshal(facts)
			ragJSON = string(data)
		}
		if err := h.db.Create(&models.LiveMessage{AvatarID: avatar.ID, SenderID: userID, Username: avatar.Name, Role: "bot", Content: reply, RAGHit: len(facts) > 0, RAGSources: ragJSON}).Error; err != nil {
			log.Printf("[QWEN_STREAM] persist reply: %v", err)
		}
	}
	_ = h.q.TrimList(ctx, liveHistoryKey(avatar.ID), -200, -1)
	return reply, len(spoken)
}
