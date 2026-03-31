package main

import (
    "context"
    "encoding/json"
    "fmt"
    "io"
    "log"
    "net/http"
    "os"
    "path/filepath"

    "github.com/go-redis/redis/v8"
    "github.com/google/uuid"
)

type GatewayServer struct {
    redisClient *redis.Client
    sharedVol   string
}

func NewGatewayServer() *GatewayServer {
    redisAddr := os.Getenv("REDIS_ADDR")
    if redisAddr == "" { redisAddr = "localhost:6379" }
    return &GatewayServer{
        redisClient: redis.NewClient(&redis.Options{Addr: redisAddr}),
        sharedVol:   os.Getenv("SHARED_VOL"),
    }
}

func (s *GatewayServer) HandleUpload(w http.ResponseWriter, r *http.Request) {
    // --- CORS SECURITY FIX ---
    w.Header().Set("Access-Control-Allow-Origin", "*")
    w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
    w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

    // If the browser is just doing a preflight check, say OK and stop here
    if r.Method == http.MethodOptions {
        w.WriteHeader(http.StatusOK)
        return
    }

    // Block anything that isn't a POST request
    if r.Method != http.MethodPost { 
        http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
        return 
    }
    // -------------------------

    r.ParseMultipartForm(10 << 20)
    file, header, _ := r.FormFile("document")
    defer file.Close()

    taskID := uuid.New().String()
    ext := filepath.Ext(header.Filename)
    savePath := filepath.Join(s.sharedVol, fmt.Sprintf("%s%s", taskID, ext))

    out, _ := os.Create(savePath)
    defer out.Close()
    io.Copy(out, file)

    ctx := context.Background()
    s.redisClient.LPush(ctx, "document_queue", savePath)
    
    // Send success response back to the frontend
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(http.StatusAccepted)
    json.NewEncoder(w).Encode(map[string]string{"status": "queued", "task_id": taskID})
}

func main() {
    server := NewGatewayServer()
    mux := http.NewServeMux()
    mux.HandleFunc("/api/v1/upload", server.HandleUpload)
    log.Println("Go Gateway running on :8080")
    http.ListenAndServe(":8080", mux)
}