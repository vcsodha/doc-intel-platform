package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/go-redis/redis/v8"
	"github.com/google/uuid"
	_ "github.com/lib/pq"
)

const (
	streamKey = "documents:stream"
	maxUpload = 10 << 20 // 10 MiB
)

type GatewayServer struct {
	redis     *redis.Client
	db        *sql.DB
	sharedVol string
}

func NewGatewayServer() (*GatewayServer, error) {
	redisAddr := getenv("REDIS_ADDR", "localhost:6379")
	dbURL := getenv("DATABASE_URL",
		"postgres://admin:password@localhost:5432/doc_intel?sslmode=disable")

	db, err := sql.Open("postgres", dbURL)
	if err != nil {
		return nil, fmt.Errorf("open db: %w", err)
	}
	db.SetMaxOpenConns(10)

	return &GatewayServer{
		redis:     redis.NewClient(&redis.Options{Addr: redisAddr}),
		db:        db,
		sharedVol: getenv("SHARED_VOL", "/shared_data"),
	}, nil
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func setCORS(w http.ResponseWriter) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
}

func (s *GatewayServer) HandleUpload(w http.ResponseWriter, r *http.Request) {
	setCORS(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	if err := r.ParseMultipartForm(maxUpload); err != nil {
		http.Error(w, "could not parse upload", http.StatusBadRequest)
		return
	}
	file, header, err := r.FormFile("document")
	if err != nil {
		http.Error(w, "missing 'document' file field", http.StatusBadRequest)
		return
	}
	defer file.Close()

	taskID := uuid.New().String()
	ext := filepath.Ext(header.Filename)
	storedName := taskID + ext
	savePath := filepath.Join(s.sharedVol, storedName)

	out, err := os.Create(savePath)
	if err != nil {
		http.Error(w, "could not store file", http.StatusInternalServerError)
		log.Printf("create %s: %v", savePath, err)
		return
	}
	defer out.Close()
	if _, err := io.Copy(out, file); err != nil {
		http.Error(w, "could not write file", http.StatusInternalServerError)
		log.Printf("write %s: %v", savePath, err)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	if _, err := s.db.ExecContext(ctx,
		`INSERT INTO documents (task_id, filename, status) VALUES ($1, $2, 'QUEUED')`,
		taskID, header.Filename,
	); err != nil {
		http.Error(w, "could not record task", http.StatusInternalServerError)
		log.Printf("insert task %s: %v", taskID, err)
		return
	}

	if err := s.redis.XAdd(ctx, &redis.XAddArgs{
		Stream: streamKey,
		Values: map[string]interface{}{
			"task_id":  taskID,
			"filepath": savePath,
			"filename": header.Filename,
			"attempts": "0",
		},
	}).Err(); err != nil {
		http.Error(w, "could not enqueue task", http.StatusInternalServerError)
		log.Printf("xadd %s: %v", taskID, err)
		return
	}
	s.redis.Incr(ctx, "metrics:received")

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"status": "queued", "task_id": taskID,
	})
}

func (s *GatewayServer) HandleMetrics(w http.ResponseWriter, r *http.Request) {
	setCORS(w)
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	get := func(key string) int64 {
		v, err := s.redis.Get(ctx, key).Result()
		if err != nil {
			return 0
		}
		n, _ := strconv.ParseInt(v, 10, 64)
		return n
	}

	completed := get("metrics:completed")
	needsReview := get("metrics:needs_review")
	failed := get("metrics:failed")
	terminal := completed + needsReview + failed

	successRate := 1.0
	if terminal > 0 {
		successRate = float64(completed+needsReview) / float64(terminal)
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"received":      get("metrics:received"),
		"completed":     completed,
		"needs_review":  needsReview,
		"failed":        failed,
		"retried":       get("metrics:retried"),
		"dead_lettered": get("metrics:dead_lettered"),
		"success_rate":  successRate,
	})
}

func (s *GatewayServer) HandleHealth(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	var problems []string
	if err := s.redis.Ping(ctx).Err(); err != nil {
		problems = append(problems, "redis")
	}
	if err := s.db.PingContext(ctx); err != nil {
		problems = append(problems, "postgres")
	}
	if len(problems) > 0 {
		http.Error(w, fmt.Sprintf("degraded: %v", problems), http.StatusServiceUnavailable)
		return
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func main() {
	server, err := NewGatewayServer()
	if err != nil {
		log.Fatal(err)
	}

	for i := 0; i < 15; i++ {
		if err = server.db.Ping(); err == nil {
			break
		}
		time.Sleep(2 * time.Second)
	}
	if err != nil {
		log.Printf("warning: db not reachable at startup: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/upload", server.HandleUpload)
	mux.HandleFunc("/api/v1/metrics", server.HandleMetrics)
	mux.HandleFunc("/healthz", server.HandleHealth)

	log.Println("Go Gateway running on :8080")
	if err := http.ListenAndServe(":8080", mux); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
