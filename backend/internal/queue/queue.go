package queue

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// TaskPayload is the JSON message pushed to Redis and consumed by the
// Python AI worker. It carries S3 keys instead of local paths so the
// services stay decoupled from each other's filesystems.
type TaskPayload struct {
	TaskID         uint   `json:"taskId"`
	AvatarID       uint   `json:"avatarId"`
	ScriptText     string `json:"scriptText"`
	ImageS3Key     string `json:"imageS3Key"`
	BaseVideoS3Key string `json:"baseVideoS3Key,omitempty"`
	TtsS3Key       string `json:"ttsS3Key,omitempty"`
	VoiceID        string `json:"voiceId,omitempty"`
}

// LiveControlPayload tells the streaming worker to start/stop the continuous
// FFmpeg pipe for an avatar's live session.
type LiveControlPayload struct {
	Action         string `json:"action"` // "start" | "stop"
	AvatarID       uint   `json:"avatarId"`
	StreamID       string `json:"streamId"`
	ImageS3Key     string `json:"imageS3Key"`
	BaseVideoS3Key string `json:"baseVideoS3Key,omitempty"`
	// IdleVideos lists the scene videos pushed while idle (switching between
	// them every IdleSwitchSeconds or at random); BaseVideoS3Key stays as the
	// first/fallback entry for older workers.
	IdleVideos        []string        `json:"idleVideos,omitempty"`
	IdleSwitchMode    string          `json:"idleSwitchMode,omitempty"` // "interval" | "random"
	IdleSwitchSeconds int             `json:"idleSwitchSeconds,omitempty"`
	VoiceID           string          `json:"voiceId,omitempty"`
	LiveSettings      json.RawMessage `json:"liveSettings,omitempty"`
}

// AvatarInitPayload tells the worker to pre-process a newly created avatar
// into a silent base driving video (LivePortrait) and store it in S3.
type AvatarInitPayload struct {
	AvatarID   uint   `json:"avatarId"`
	ImageS3Key string `json:"imageS3Key"`
}

// VideoGenPayload tells the worker to generate a scene video through a
// provider (liveportrait now; comfyui etc. later) and write the result back
// to the scene_videos row via the completion webhook.
type VideoGenPayload struct {
	SceneVideoID     uint            `json:"sceneVideoId"`
	AvatarID         uint            `json:"avatarId"`
	SceneID          uint            `json:"sceneId"`
	SourceImageS3Key string          `json:"sourceImageS3Key"`
	Provider         string          `json:"provider"`
	Settings         json.RawMessage `json:"settings,omitempty"`
}

// KnowledgeIngestPayload tells the Python RAG worker to extract, chunk and
// embed one avatar's knowledge source (text or .txt/.pdf uploaded to S3).
// The worker downloads the file from S3, so the local filePath in the plan
// maps to the project's cross-service convention: S3 keys only.
type KnowledgeIngestPayload struct {
	Type        string `json:"type"` // "ingest_knowledge"
	AvatarID    uint   `json:"avatarId"`
	KnowledgeID uint   `json:"knowledgeId"`
	S3Key       string `json:"s3Key"`
	Filename    string `json:"filename,omitempty"`
}

// Queue wraps a Redis list used as a FIFO task queue.
type Queue struct {
	client *redis.Client
	key    string
}

func (q *Queue) ReadValue(ctx context.Context, key string) (string, error) {
	value, err := q.client.Get(ctx, key).Result()
	if err == redis.Nil {
		return "", nil
	}
	return value, err
}

func (q *Queue) WriteValue(ctx context.Context, key, value string) error {
	return q.client.Set(ctx, key, value, 0).Err()
}

// PushBatchBounded accepts an entire narration or none of it, preserving order.
func (q *Queue) PushBatchBounded(ctx context.Context, key string, values []string, limit int) error {
	args := []any{limit}
	for _, value := range values {
		args = append(args, value)
	}
	n, err := q.client.Eval(ctx, `if redis.call('LLEN', KEYS[1]) + #ARGV - 1 > tonumber(ARGV[1]) then return -1 end for i=2,#ARGV do redis.call('RPUSH', KEYS[1], ARGV[i]) end return 1`, []string{key}, args...).Int()
	if err != nil {
		return err
	}
	if n < 0 {
		return fmt.Errorf("live queue capacity reached")
	}
	return nil
}

func New(addr, password string, db int, key string) *Queue {
	client := redis.NewClient(&redis.Options{
		Addr:         addr,
		Password:     password,
		DB:           db,
		DialTimeout:  5 * time.Second,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 5 * time.Second,
	})
	return &Queue{client: client, key: key}
}

// Push appends a task payload to the queue (worker uses BLPOP).
func (q *Queue) Push(ctx context.Context, payload TaskPayload) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	return q.client.RPush(ctx, q.key, data).Err()
}

// PushTo appends an arbitrary JSON value to a named Redis list. Used by the
// streaming queue (talking_avatar:stream_tasks) alongside the task queue.
func (q *Queue) PushTo(ctx context.Context, key string, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return q.client.RPush(ctx, key, data).Err()
}

// RPushList appends a raw string to a named Redis list (e.g. a live_queue).
func (q *Queue) RPushList(ctx context.Context, key, value string) error {
	return q.client.RPush(ctx, key, value).Err()
}

// ListLen returns the length of a named Redis list.
func (q *Queue) ListLen(ctx context.Context, key string) (int64, error) {
	return q.client.LLen(ctx, key).Result()
}

// ListRange returns a slice of a named Redis list (start inclusive).
func (q *Queue) ListRange(ctx context.Context, key string, start, stop int64) ([]string, error) {
	return q.client.LRange(ctx, key, start, stop).Result()
}

// TrimList keeps only the given slice of a Redis list (used to cap history).
func (q *Queue) TrimList(ctx context.Context, key string, start, stop int64) error {
	return q.client.LTrim(ctx, key, start, stop).Err()
}

// Remove deletes an exact string value from a Redis list (LRem, count 1).
func (q *Queue) Remove(ctx context.Context, key, value string) error {
	return q.client.LRem(ctx, key, 1, value).Err()
}

// DeleteKey removes a whole Redis key (e.g. a per-avatar live queue).
func (q *Queue) DeleteKey(ctx context.Context, key string) error {
	return q.client.Del(ctx, key).Err()
}

// PushBounded atomically rejects new work when a live queue reaches capacity.
func (q *Queue) PushBounded(ctx context.Context, key, value string, limit int64) error {
	n, err := q.client.Eval(ctx, `if redis.call('LLEN', KEYS[1]) >= tonumber(ARGV[1]) then return -1 end return redis.call('RPUSH', KEYS[1], ARGV[2])`, []string{key}, limit, value).Int64()
	if err != nil {
		return err
	}
	if n < 0 {
		return fmt.Errorf("live queue capacity reached")
	}
	return nil
}

func (q *Queue) WriteValueTTL(ctx context.Context, key, value string, ttl time.Duration) error {
	return q.client.Set(ctx, key, value, ttl).Err()
}
func (q *Queue) Claim(ctx context.Context, key string, ttl time.Duration) (bool, error) {
	return q.client.SetNX(ctx, key, "1", ttl).Result()
}
func (q *Queue) IncrementTTL(ctx context.Context, key string, n int64, ttl time.Duration) (int64, error) {
	return q.client.Eval(ctx, `local n=redis.call('INCRBY',KEYS[1],ARGV[1]); redis.call('PEXPIRE',KEYS[1],ARGV[2]); return n`, []string{key}, n, ttl.Milliseconds()).Int64()
}
func (q *Queue) PrependBounded(ctx context.Context, key, value string, limit int, ttl time.Duration) error {
	return q.client.Eval(ctx, `redis.call('LPUSH',KEYS[1],ARGV[1]); redis.call('LTRIM',KEYS[1],0,tonumber(ARGV[2])-1); redis.call('PEXPIRE',KEYS[1],ARGV[3]); return 1`, []string{key}, value, limit, ttl.Milliseconds()).Err()
}
