package config

import "testing"

func TestQwenConfiguration(t *testing.T) {
	t.Setenv("OPENAI_API", "")
	t.Setenv("V2_QWEN_BASE_URL", "http://local-qwen:18003/v1")
	t.Setenv("V2_QWEN_MODEL", "qwen35")
	t.Setenv("V2_QWEN_API_KEY", "test")
	cfg := Load()
	if cfg.OpenAIAPI != "chat_completions" || cfg.OpenAIModel != "qwen35" || cfg.OpenAIAPIKey != "test" || cfg.OpenAIBaseURL != "http://local-qwen:18003/v1" {
		t.Fatal("Qwen configuration was not selected")
	}
}

func TestResponsesConfigurationPreserved(t *testing.T) {
	t.Setenv("OPENAI_API", "")
	t.Setenv("V2_QWEN_BASE_URL", "")
	t.Setenv("V2_QWEN_MODEL", "")
	t.Setenv("V2_QWEN_API_KEY", "")
	t.Setenv("OPENAI_MODEL", "previous-model")
	cfg := Load()
	if cfg.OpenAIAPI != "responses" || cfg.OpenAIModel != "previous-model" {
		t.Fatal("legacy configuration changed")
	}
}
