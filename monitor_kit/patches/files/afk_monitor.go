package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"
)

var afkHTTPClient = &http.Client{Timeout: 3 * time.Second}

func afkEventPort() int {
	raw := os.Getenv("AFK_EVENT_PORT")
	if raw == "" {
		return 0
	}
	port, err := strconv.Atoi(raw)
	if err != nil || port <= 0 {
		return 0
	}
	return port
}

func afkEmit(kind string, level string, message string, data map[string]interface{}) {
	port := afkEventPort()
	if port == 0 {
		return
	}
	payload := map[string]interface{}{
		"ts":      float64(time.Now().UnixNano()) / 1e9,
		"project": "MaaEnd",
		"run_id":  os.Getenv("AFK_RUN_ID"),
		"kind":    kind,
		"level":   level,
		"message": message,
	}
	if data != nil {
		payload["data"] = data
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return
	}
	url := "http://127.0.0.1:" + strconv.Itoa(port) + "/event"
	resp, err := afkHTTPClient.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		return
	}
	_ = resp.Body.Close()
}

func afkMonitorBootstrap() {
	afkEmit("log", "info", "go-service bootstrap", map[string]interface{}{
		"pid":  os.Getpid(),
		"args": os.Args,
	})
}

func afkMonitorStart() {
	afkEmit("start", "info", "go-service started", map[string]interface{}{
		"pid":     os.Getpid(),
		"version": Version,
	})
	go func() {
		ticker := time.NewTicker(20 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			afkEmit("heartbeat", "debug", "", nil)
		}
	}()
}

var afkPanicOnce sync.Once

func afkMonitorPanic(recovered interface{}) {
	afkPanicOnce.Do(func() {
		afkEmit("error", "error", "go-service panicked", map[string]interface{}{
			"pid":    os.Getpid(),
			"panic":  toAfkString(recovered),
			"source": "go-service",
		})
	})
}

func toAfkString(value interface{}) string {
	if value == nil {
		return "<nil>"
	}
	if s, ok := value.(string); ok {
		return s
	}
	if e, ok := value.(error); ok {
		return e.Error()
	}
	return "<non-string panic>"
}
