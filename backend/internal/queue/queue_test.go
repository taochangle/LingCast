package queue

import (
	"context"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// Exercise the capacity check atomically against a real disposable Redis key.
func TestBoundedLiveQueueConcurrency(t *testing.T) {
	addr := os.Getenv("REDIS_TEST_ADDR")
	if addr == "" {
		t.Skip("set REDIS_TEST_ADDR for Redis integration test")
	}
	q := New(addr, "", 0, "")
	defer q.client.Close()
	ctx := context.Background()
	key := fmt.Sprintf("lingcast:test:bounded:%d", time.Now().UnixNano())
	defer q.DeleteKey(ctx, key)
	var wg sync.WaitGroup
	var successes atomic.Int32
	for i := 0; i < 30; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if q.PushBounded(ctx, key, "test", 5) == nil {
				successes.Add(1)
			}
		}()
	}
	wg.Wait()
	n, err := q.ListLen(ctx, key)
	if err != nil || n != 5 || successes.Load() != 5 {
		t.Fatalf("length=%d successful=%d err=%v", n, successes.Load(), err)
	}
}

func TestNarrationBatchIsAtomic(t *testing.T) {
	addr := os.Getenv("REDIS_TEST_ADDR")
	if addr == "" {
		t.Skip("Redis integration")
	}
	q := New(addr, "", 0, "")
	defer q.client.Close()
	ctx := context.Background()
	key := fmt.Sprintf("lingcast:test:narration:%d", time.Now().UnixNano())
	defer q.DeleteKey(ctx, key)
	if err := q.PushBatchBounded(ctx, key, []string{"one", "two", "three"}, 5); err != nil {
		t.Fatal(err)
	}
	if err := q.PushBatchBounded(ctx, key, []string{"four", "five", "six"}, 5); err == nil {
		t.Fatal("overflow accepted")
	}
	values, err := q.ListRange(ctx, key, 0, -1)
	if err != nil || len(values) != 3 || values[0] != "one" || values[2] != "three" {
		t.Fatalf("partial batch or wrong order: %v %v", values, err)
	}
}
