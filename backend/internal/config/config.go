package config

import (
	"os"
	"strconv"
	"strings"
)

// Config aggregates all runtime configuration read from environment variables.
type Config struct {
	ServerPort    string // legacy single-entry port (kept for tooling)
	AdminPort     string
	LivePort      string
	TelegramPort  string
	WebPort       string
	SchedulerPort string
	GinMode       string

	MySQLDSN string

	RedisAddr           string
	RedisPassword       string
	RedisDB             int
	TaskQueueKey        string
	LiveControlQueueKey string
	AvatarInitQueueKey  string
	OpenAIAPIKey        string
	OpenAIBaseURL       string
	OpenAIModel         string
	OpenAIAPI           string
	LiveRenderEngine    string
	EmbedServerURL      string
	TTSServiceURL       string
	VideoGenServiceURL  string
	AdminUsername       string
	AdminPassword       string
	TgBotToken          string
	NgrokAPIURL         string
	TgWebhookURL        string
	TgMiniAppURL        string

	S3Endpoint      string
	S3AccessKey     string
	S3SecretKey     string
	S3Bucket        string
	S3UseSSL        bool
	S3PublicBaseURL string

	GoogleClientID     string
	GoogleClientSecret string
	GoogleRedirectURL  string

	CORSOrigins []string
}

func env(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func envBool(key string, fallback bool) bool {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return strings.EqualFold(v, "true") || v == "1"
	}
	return fallback
}

func splitCSV(s string) []string {
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// Load reads configuration from environment variables with sensible defaults
// so the API can also be run locally against dockerized dependencies.
func Load() Config {
	return Config{
		ServerPort:    env("API_PORT", "8080"),
		AdminPort:     env("API_ADMIN_PORT", "8081"),
		LivePort:      env("API_LIVE_PORT", "8082"),
		TelegramPort:  env("API_TELEGRAM_PORT", "8084"),
		WebPort:       env("API_WEB_PORT", "8085"),
		SchedulerPort: env("API_SCHEDULER_PORT", "8083"),
		GinMode:       env("GIN_MODE", "debug"),
		MySQLDSN: env("MYSQL_DSN",
			"talking:talking123@tcp(127.0.0.1:3306)/talking_avatar?charset=utf8mb4&parseTime=True&loc=Local"),

		RedisAddr:           env("REDIS_ADDR", "127.0.0.1:6379"),
		RedisPassword:       os.Getenv("REDIS_PASSWORD"),
		RedisDB:             envInt("REDIS_DB", 0),
		TaskQueueKey:        env("TASK_QUEUE_KEY", "talking_avatar:tasks"),
		LiveControlQueueKey: env("LIVE_CONTROL_QUEUE_KEY", "talking_avatar:live_control"),
		AvatarInitQueueKey:  env("AVATAR_INIT_QUEUE_KEY", "talking_avatar:avatar_init"),
		OpenAIAPIKey:        env("V2_QWEN_API_KEY", os.Getenv("OPENAI_API_KEY")),
		OpenAIBaseURL:       env("V2_QWEN_BASE_URL", env("OPENAI_BASE_URL", "https://api.deepseek.com")),
		OpenAIModel:         env("V2_QWEN_MODEL", env("OPENAI_MODEL", "deepseek-v4-flash")),
		LiveRenderEngine:    env("LIPSYNC_ENGINE", "wav2lip"),
		OpenAIAPI:           env("OPENAI_API", llmAPIDefault()),
		EmbedServerURL:      env("EMBED_SERVER_URL", "http://host.docker.internal:8090"),
		TTSServiceURL:       env("TTS_SERVICE_URL", "http://service-tts:8002"),
		VideoGenServiceURL:  env("VIDEO_GEN_SERVICE_URL", "http://service-video-gen:8003"),
		AdminUsername:       env("ADMIN_USERNAME", "admin"),
		AdminPassword:       env("ADMIN_PASSWORD", "admin123"),
		TgBotToken:          os.Getenv("TG_BOT_TOKEN"),
		NgrokAPIURL:         env("NGROK_API_URL", "http://ngrok:4040"),
		TgWebhookURL:        os.Getenv("TG_WEBHOOK_URL"),
		TgMiniAppURL:        os.Getenv("TG_MINIAPP_URL"),

		S3Endpoint:      env("S3_ENDPOINT", "http://127.0.0.1:9000"),
		S3AccessKey:     env("S3_ACCESS_KEY", "rustfsadmin"),
		S3SecretKey:     env("S3_SECRET_KEY", "rustfsadmin"),
		S3Bucket:        env("S3_BUCKET", "talking-avatar"),
		S3UseSSL:        envBool("S3_USE_SSL", false),
		S3PublicBaseURL: env("S3_PUBLIC_BASE_URL", ""),

		GoogleClientID:     os.Getenv("GOOGLE_CLIENT_ID"),
		GoogleClientSecret: os.Getenv("GOOGLE_CLIENT_SECRET"),
		GoogleRedirectURL:  env("GOOGLE_REDIRECT_URL", "http://localhost:8085/api/auth/google/callback"),

		CORSOrigins: splitCSV(env("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001,http://localhost:5173,http://localhost:8080,https://admin.lingcast.com")),
	}
}

func llmAPIDefault() string {
	if os.Getenv("V2_QWEN_BASE_URL") != "" {
		return "chat_completions"
	}
	return "responses"
}
