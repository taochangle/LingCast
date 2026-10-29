package queue

import (
	"fmt"
	"os"
	"sync"
	"testing"
	"time"
)

func TestRotationIntegration(t *testing.T) {
	addr := os.Getenv("REDIS_TEST_ADDR")
	if addr == "" {
		t.Skip("isolated Redis required")
	}
	q := New(addr, "", 0, "")
	key := fmt.Sprintf("rotation-test:%d", time.Now().UnixNano())
	defer q.DeleteKey(t.Context(), key)
	defer q.DeleteKey(t.Context(), key+":last")
	options := []string{"甲", "乙", "丙"}
	last := ""
	for round := 0; round < 4; round++ {
		seen := map[string]bool{}
		for i := 0; i < 3; i++ {
			speech, err := q.PopRotation(t.Context(), key, options, time.Minute)
			if err != nil || seen[speech] || (i == 0 && speech == last) {
				t.Fatalf("round=%d value=%s error=%v", round, speech, err)
			}
			seen[speech] = true
			last = speech
		}
	}
	// Concurrent consumers share one bag without duplicate selection.
	var wg sync.WaitGroup
	values := make(chan string, 3)
	for i := 0; i < 3; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			value, err := q.PopRotation(t.Context(), key, options, time.Minute)
			if err != nil {
				t.Error(err)
			}
			values <- value
		}()
	}
	wg.Wait()
	close(values)
	seen := map[string]bool{}
	for value := range values {
		if seen[value] {
			t.Fatal("duplicate concurrent selection")
		}
		seen[value] = true
	}
	ttl, err := q.client.TTL(t.Context(), key+":last").Result()
	if err != nil || ttl <= 0 || ttl > time.Minute {
		t.Fatal("missing expiry", ttl, err)
	}
	other, err := q.PopRotation(t.Context(), key+":other-session", options, time.Minute)
	defer q.DeleteKey(t.Context(), key+":other-session")
	defer q.DeleteKey(t.Context(), key+":other-session:last")
	if err != nil || other != "甲" {
		t.Fatal("session not isolated", other, err)
	}
}
