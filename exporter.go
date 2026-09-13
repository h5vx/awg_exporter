package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"runtime/debug"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/h5vx/awg-exporter/docker"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

/* =========================
   Utils / Logger
   ========================= */

func getEnv(key string, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func getEnvInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return def
}

func getEnvBool(key string, def bool) bool {
	if v := strings.ToLower(os.Getenv(key)); v != "" {
		return v == "true" || v == "1" || v == "yes"
	}
	return def
}

/* =========================
   AWG show parsing
   ========================= */

var (
	reDays    = regexp.MustCompile(`(\d+)\s+days?`)
	reHours   = regexp.MustCompile(`(\d+)\s+hours?`)
	reMinutes = regexp.MustCompile(`(\d+)\s+minutes?`)
	reSeconds = regexp.MustCompile(`(\d+)\s+seconds?`)
)

func parseTimeString(s string) int64 {
	now := time.Now()

	get := func(re *regexp.Regexp) int {
		m := re.FindStringSubmatch(s)
		if len(m) == 2 {
			i, _ := strconv.Atoi(m[1])
			return i
		}
		return 0
	}

	delta := time.Duration(get(reDays))*24*time.Hour +
		time.Duration(get(reHours))*time.Hour +
		time.Duration(get(reMinutes))*time.Minute +
		time.Duration(get(reSeconds))*time.Second

	return now.Add(-delta).Unix()
}

func toBytes(s string) int64 {
	parts := strings.Split(strings.TrimSpace(s), " ")
	if len(parts) < 2 {
		return 0
	}

	value, _ := strconv.ParseFloat(parts[0], 64)
	unit := parts[1]

	mult := map[string]float64{
		"B":   1,
		"KiB": 1024,
		"MiB": 1024 * 1024,
		"GiB": 1024 * 1024 * 1024,
		"TiB": 1024 * 1024 * 1024 * 1024,
	}

	return int64(value * mult[unit])
}

type Peer struct {
	Peer            string
	Received        int64
	Sent            int64
	LatestHandshake int64
}

func parseAwgShow(text string) []Peer {
	var peers []Peer
	var cur Peer

	lines := strings.SplitSeq(text, "\n")
	for line := range lines {
		line = strings.TrimSpace(line)

		if line == "" {
			if cur.Peer != "" {
				peers = append(peers, cur)
			}
			cur = Peer{}
			continue
		}

		parts := strings.SplitN(line, ": ", 2)
		if len(parts) != 2 {
			continue
		}

		key := strings.ReplaceAll(parts[0], " ", "_")
		val := parts[1]

		switch key {
		case "peer":
			cur.Peer = val
		case "transfer":
			t := strings.Split(val, ", ")
			if len(t) == 2 {
				cur.Received = toBytes(t[0])
				cur.Sent = toBytes(t[1])
			}
		case "latest_handshake":
			cur.LatestHandshake = parseTimeString(val)
		}
	}

	if cur.Peer != "" {
		peers = append(peers, cur)
	}

	return peers
}

/* =========================
   Exporter
   ========================= */

type Exporter struct {
	cfg       Config
	ctx       context.Context
	cancel    context.CancelFunc
	registry  *prometheus.Registry
	sent      *prometheus.CounterVec
	received  *prometheus.CounterVec
	handshake *prometheus.GaugeVec
	nErrors   *prometheus.CounterVec
	status    prometheus.Gauge

	lastSent     map[string]int64
	lastReceived map[string]int64
}

type Config struct {
	ScrapeInterval      time.Duration
	HTTPPort            int
	HTTPHost            string
	OpsMode             string
	ClientsTableEnabled bool
	ClientsTableFile    string
	AwgCmd              []string
	ContainerName       string
}

func NewExporter(cfg Config) *Exporter {
	reg := prometheus.NewRegistry()

	e := &Exporter{
		cfg:      cfg,
		registry: reg,
	}
	e.lastSent = make(map[string]int64)
	e.lastReceived = make(map[string]int64)

	e.sent = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "awg_sent_bytes",
			Help: "Client sent bytes",
		},
		[]string{"peer", "client_name"},
	)

	e.received = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "awg_received_bytes",
			Help: "Client received bytes",
		},
		[]string{"peer", "client_name"},
	)

	e.handshake = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{Name: "awg_latest_handshake_seconds"},
		[]string{"peer", "client_name"},
	)

	e.status = prometheus.NewGauge(
		prometheus.GaugeOpts{Name: "awg_exporter_status"},
	)

	e.nErrors = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "awg_exporter_errors",
			Help: "Exporter errors",
		},
		[]string{"error_type"},
	)

	reg.MustRegister(e.sent, e.received, e.handshake, e.status, e.nErrors)

	e.ctx, e.cancel = context.WithCancel(context.Background())
	return e
}

func (e *Exporter) runAwgShow() (string, error) {
	stdout, stderr, code, err := docker.ExecInContainerByName(
		e.ctx,
		e.cfg.ContainerName,
		e.cfg.AwgCmd,
	)
	if err != nil {
		return "", err
	}
	if code != 0 {
		return "", errors.New(stderr)
	}
	return stdout, nil
}

func (e *Exporter) readClientsTable() []map[string]any {
	stdout, _, _, err := docker.ExecInContainerByName(
		e.ctx,
		e.cfg.ContainerName,
		[]string{"cat", e.cfg.ClientsTableFile},
	)
	if err != nil {
		log.Printf("clients table error: %v", err)
		return nil
	}

	var data []map[string]any
	if err := json.Unmarshal([]byte(stdout), &data); err != nil {
		log.Printf("clients table json error: %v", err)
		log.Printf("\nRead JSON:\n %s\n", stdout)
		log.Printf("-------------------------------")
	}
	return data
}

func (e *Exporter) Update() {
	out, err := e.runAwgShow()
	if err != nil {
		log.Printf("awg show error: %v", err)
		e.status.Set(0)
		e.nErrors.WithLabelValues("awg_show_exec").Add(1.0)
		return
	}

	peers := parseAwgShow(out)
	if len(peers) == 0 {
		e.status.Set(0)
		e.nErrors.WithLabelValues("awg_show_parse").Add(1.0)
		return
	}

	var clients []map[string]any
	if e.cfg.ClientsTableEnabled {
		clients = e.readClientsTable()
	}

	for _, p := range peers {
		clientName := "unidentified"

		for _, c := range clients {
			if c["clientId"] == p.Peer {
				if ud, ok := c["userData"].(map[string]any); ok {
					if n, ok := ud["clientName"].(string); ok {
						clientName = n
					}
				}
			}
		}

		key := p.Peer + "|" + clientName
		if prev, ok := e.lastSent[key]; ok {
			delta := p.Sent - prev
			if delta >= 0 {
				e.sent.WithLabelValues(p.Peer, clientName).Add(float64(delta))
			}
		}
		e.lastSent[key] = p.Sent

		if prev, ok := e.lastReceived[key]; ok {
			delta := p.Received - prev
			if delta >= 0 {
				e.received.WithLabelValues(p.Peer, clientName).Add(float64(delta))
			}
		}
		e.lastReceived[key] = p.Received
		e.handshake.WithLabelValues(p.Peer, clientName).Set(float64(p.LatestHandshake))
	}

	e.status.Set(1)
}

func (e *Exporter) Run() {
	if e.cfg.OpsMode == "http" {
		httpHandler := promhttp.HandlerFor(e.registry, promhttp.HandlerOpts{})
		log.Printf("Server is running on %s:%d", e.cfg.HTTPHost, e.cfg.HTTPPort)
		go func() {
			log.Fatal(
				http.ListenAndServe(
					fmt.Sprintf("%s:%d", e.cfg.HTTPHost, e.cfg.HTTPPort),
					httpHandler,
				),
			)
		}()
	}

	e.Update()

	t := time.NewTicker(e.cfg.ScrapeInterval)
	defer t.Stop()

	for {
		select {
		case <-t.C:
			e.Update()
		case <-e.ctx.Done():
			return
		}
	}
}

/* =========================
   main
   ========================= */

func main() {
	// Soft heap cap so GC runs before RSS grows; override with GOMEMLIMIT env.
	if os.Getenv("GOMEMLIMIT") == "" {
		debug.SetMemoryLimit(10 << 20)
	}

	cfg := Config{
		ScrapeInterval:      time.Duration(getEnvInt("AWG_EXPORTER_SCRAPE_INTERVAL", 60)) * time.Second,
		HTTPPort:            getEnvInt("AWG_EXPORTER_HTTP_PORT", 9351),
		HTTPHost:            getEnv("AWG_EXPORTER_HTTP_HOST", "127.0.0.1"),
		OpsMode:             getEnv("AWG_EXPORTER_OPS_MODE", "http"),
		ClientsTableEnabled: getEnvBool("AWG_EXPORTER_CLIENTS_TABLE_ENABLED", true),
		ClientsTableFile:    getEnv("AWG_EXPORTER_CLIENTS_TABLE_FILE", "/opt/amnezia/awg/clientsTable"),
		AwgCmd:              strings.Split(getEnv("AWG_EXPORTER_AWG_SHOW_EXEC", "wg show all"), " "),
		ContainerName:       getEnv("AWG_CONTAINER_NAME", "amnezia-awg"),
	}

	log.Printf("Amnezia WG exporter started in %s mode", cfg.OpsMode)

	exp := NewExporter(cfg)

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)

	go exp.Run()

	<-sig
	log.Println("Shutdown requested")
	exp.cancel()
}
