package live

import (
	"encoding/json"
	"strings"
	"testing"

	"talkingavatar/backend/internal/models"
)

func TestChatSystemPromptIncludesProfile(t *testing.T) {
	age, height, weight := 25, 165, 50
	persona, _ := json.Marshal(models.PersonaProfile{
		Age:                &age,
		HeightCm:           &height,
		WeightKg:           &weight,
		Ethnicity:          "汉族",
		RelationshipStatus: "单身",
		Personality:        "活泼开朗",
	})
	a := models.Avatar{
		Name:    "翠花",
		Persona: string(persona),
	}
	prompt := chatSystemPrompt(a, "zh", nil, nil)
	for _, want := range []string{
		"翠花",
		"年龄 25 岁",
		"身高 165 厘米",
		"体重 50 公斤",
		"族裔 汉族",
		"感情状态 单身",
		"性格 活泼开朗",
	} {
		if !strings.Contains(prompt, want) {
			t.Fatalf("prompt missing %q:\n%s", want, prompt)
		}
	}
}

func TestChatSystemPromptEmptyProfile(t *testing.T) {
	prompt := chatSystemPrompt(models.Avatar{Name: "小美"}, "zh", nil, nil)
	if strings.Contains(prompt, "人物设定") {
		t.Fatalf("empty profile should not include a persona block:\n%s", prompt)
	}
}

func TestChatSystemPromptEnglish(t *testing.T) {
	age := 25
	persona, _ := json.Marshal(models.PersonaProfile{Age: &age})
	a := models.Avatar{
		Name:    "Xiaomei",
		Persona: string(persona),
	}
	prompt := chatSystemPrompt(a, "en", nil, nil)
	for _, want := range []string{
		"digital human streamer named \"Xiaomei\"",
		"Age 25",
		"conversational English",
	} {
		if !strings.Contains(prompt, want) {
			t.Fatalf("en prompt missing %q:\n%s", want, prompt)
		}
	}
}

func TestChatSystemPromptActionVideos(t *testing.T) {
	a := models.Avatar{Name: "翠花"}
	zh := chatSystemPrompt(a, "zh", nil, nil,
		sceneVideoOption{S3Key: "base_videos/scene1/laugh.mp4", Description: "开心大笑"},
		sceneVideoOption{S3Key: "base_videos/scene1/think.mp4", Description: "认真思考"},
	)
	for _, want := range []string{
		"[当前场景可用的动作视频]",
		"<action:base_videos/scene1/laugh.mp4>",
		"开心大笑",
		"<action:base_videos/scene1/think.mp4>",
	} {
		if !strings.Contains(zh, want) {
			t.Fatalf("zh prompt missing %q: %s", want, zh)
		}
	}
	en := chatSystemPrompt(a, "en", nil, nil,
		sceneVideoOption{S3Key: "base_videos/scene1/laugh.mp4", Description: "Laughing happily"},
	)
	for _, want := range []string{
		"[Available Action Videos in Current Scene]",
		"<action:base_videos/scene1/laugh.mp4>",
		"Laughing happily",
	} {
		if !strings.Contains(en, want) {
			t.Fatalf("en prompt missing %q: %s", want, en)
		}
	}
}

func TestParseActionTag(t *testing.T) {
	clean, key := parseActionTag("<action:base_videos/scene1/laugh.mp4> 哈哈，这也太好玩了！")
	if key != "base_videos/scene1/laugh.mp4" || clean != "哈哈，这也太好玩了！" {
		t.Fatalf("tag not parsed: key=%q clean=%q", key, clean)
	}
	// Whitespace before the tag is tolerated; stray markers are stripped.
	clean, key = parseActionTag("   <action:base_videos/a.mp4> 普通句子")
	if key != "base_videos/a.mp4" || clean != "普通句子" {
		t.Fatalf("tag with leading space not parsed: key=%q clean=%q", key, clean)
	}
	clean, key = parseActionTag("没有任何标记的句子")
	if key != "" || clean != "没有任何标记的句子" {
		t.Fatalf("plain sentence changed: key=%q clean=%q", key, clean)
	}
	clean, key = parseActionTag("句中出现<action:base_videos/x.mp4>标记")
	if key != "" || strings.Contains(clean, "<action") {
		t.Fatalf("stray tag not stripped: key=%q clean=%q", key, clean)
	}
}

func TestChatSystemPromptRAGAndMemory(t *testing.T) {
	a := models.Avatar{Name: "翠花"}
	memory := []models.LiveMessage{
		{Role: "user", Content: "你喜欢什么颜色？"},
		{Role: "bot", Content: "我喜欢紫色。"},
	}
	facts := []string{"本店商品满99元包邮，七天无理由退货。"}

	zh := chatSystemPrompt(a, "zh", memory, facts)
	for _, want := range []string{"私有知识库", "本店商品满99元包邮", "最近", "viewer", "你喜欢什么颜色？", "past_reply", "我喜欢紫色。"} {
		if !strings.Contains(zh, want) {
			t.Fatalf("zh prompt missing %q:\n%s", want, zh)
		}
	}

	en := chatSystemPrompt(a, "en", memory, facts)
	for _, want := range []string{"private knowledge base", "本店商品满99元包邮", "Recent conversation", "past_reply", "我喜欢紫色。"} {
		if !strings.Contains(en, want) {
			t.Fatalf("en prompt missing %q:\n%s", want, en)
		}
	}
}

func TestSentenceCollectorOrderAndBoundaries(t *testing.T) {
	cases := []struct {
		name   string
		deltas []string
		want   []string
	}{
		{
			// Boundary set is [。，！？.!?] — English commas are NOT split.
			name:   "chinese and english boundaries, order preserved",
			deltas: []string{"你好，欢迎", "来到直播间！今天给大家", "讲个故事。See you, bye!"},
			want:   []string{"你好，", "欢迎来到直播间！", "今天给大家讲个故事。", "See you, bye!"},
		},
		{
			name:   "short fragments merged after chinese comma",
			deltas: []string{"好，", "吧", "。好的！"},
			// "好，" (2 runes) is long enough, "吧。" (2 runes) too short is
			// merged with nothing, then "好的！" submits on its own.
			want: []string{"好，", "吧。", "好的！"},
		},
		{
			name:   "flush trailing sentence without boundary",
			deltas: []string{"第一句。第二句没有标点"},
			want:   []string{"第一句。", "第二句没有标点"},
		},
		{
			name:   "single punctuation fragment kept for next sentence",
			deltas: []string{"我", "，", "很"}, // "我，" is 2 runes -> submitted; "很" too short to flush
			want:   []string{"我，"},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sc := &sentenceCollector{}
			var got []string
			for _, d := range tc.deltas {
				got = append(got, sc.feed(d)...)
			}
			got = append(got, sc.flush()...)
			if len(got) != len(tc.want) {
				t.Fatalf("got %d sentences %q, want %d %q", len(got), got, len(tc.want), tc.want)
			}
			for i := range got {
				if got[i] != tc.want[i] {
					t.Fatalf("sentence %d = %q, want %q (all: %q)", i, got[i], tc.want[i], got)
				}
			}
		})
	}
}
