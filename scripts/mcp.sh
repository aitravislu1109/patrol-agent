#!/usr/bin/env bash
# 在本機啟動 / 停止 / 檢查 MCP server。
#
#   scripts/mcp.sh start    啟動已啟用的 MCP server（背景執行，日誌在 logs/）
#   scripts/mcp.sh stop     停止
#   scripts/mcp.sh status   檢查各 MCP 端點是否可連線
#
# 依 patrol_agent/.env 決定要啟動哪些：
#   Kubernetes、Prometheus 固定啟動；
#   ENABLE_ELASTICSEARCH=true 才啟動 Elasticsearch MCP；
#   ENABLE_DYNATRACE=true 才啟動 Dynatrace MCP。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_DIR="$ROOT/patrol_agent"
LOG_DIR="$ROOT/logs"
RUN_DIR="$ROOT/.run"
ENV_FILE="$AGENT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "找不到 $ENV_FILE，請先執行：cp patrol_agent/.env.example patrol_agent/.env" >&2
  exit 1
fi

K8S_MCP_PORT="${K8S_MCP_PORT:-18080}"
PROM_MCP_PORT="${PROM_MCP_PORT:-18090}"
ES_MCP_PORT="${ES_MCP_PORT:-18100}"
DT_MCP_PORT="${DT_MCP_PORT:-18110}"
PROM_MCP_IMAGE="${PROM_MCP_IMAGE:-ghcr.io/pab1it0/prometheus-mcp-server:latest}"
ES_MCP_IMAGE="${ES_MCP_IMAGE:-docker.elastic.co/mcp/elasticsearch:latest}"

is_true() { [[ "$(echo "${1:-false}" | tr '[:upper:]' '[:lower:]')" =~ ^(1|true|yes|on)$ ]]; }

start_k8s() {
  local cfg="$ROOT/scripts/k8s-mcp.toml"
  {
    echo "port = \"$K8S_MCP_PORT\""
    echo "bind_address = \"127.0.0.1\""
    echo "read_only = true"
    echo "disable_destructive = true"
    echo 'toolsets = ["core"]'
    if [[ -n "${K8S_MCP_KUBECONFIG:-}" ]]; then echo "kubeconfig = \"$K8S_MCP_KUBECONFIG\""; fi
  } > "$cfg"
  echo "[k8s] 啟動 kubernetes-mcp-server（唯讀）於 :$K8S_MCP_PORT"
  nohup npx -y kubernetes-mcp-server@latest --config "$cfg" > "$LOG_DIR/k8s-mcp.log" 2>&1 &
  echo $! > "$RUN_DIR/k8s-mcp.pid"
}

start_prometheus() {
  echo "[prometheus] 啟動 prometheus-mcp-server（Docker）於 :$PROM_MCP_PORT，PROMETHEUS_URL=$PROMETHEUS_URL"
  docker rm -f patrol-prometheus-mcp > /dev/null 2>&1 || true
  docker run -d --name patrol-prometheus-mcp \
    -p "127.0.0.1:$PROM_MCP_PORT:$PROM_MCP_PORT" \
    --add-host=host.docker.internal:host-gateway \
    -e PROMETHEUS_URL \
    -e PROMETHEUS_USERNAME -e PROMETHEUS_PASSWORD -e PROMETHEUS_TOKEN \
    -e PROMETHEUS_MCP_SERVER_TRANSPORT=http \
    -e PROMETHEUS_MCP_BIND_HOST=0.0.0.0 \
    -e PROMETHEUS_MCP_BIND_PORT="$PROM_MCP_PORT" \
    -e PROMETHEUS_DISABLE_LINKS=true \
    "$PROM_MCP_IMAGE" > /dev/null
}

start_elasticsearch() {
  echo "[elasticsearch] 啟動 Elastic MCP（Docker）於 :$ES_MCP_PORT，ES_URL=$ES_URL"
  docker rm -f patrol-elasticsearch-mcp > /dev/null 2>&1 || true
  docker run -d --name patrol-elasticsearch-mcp \
    -p "127.0.0.1:$ES_MCP_PORT:8080" \
    --add-host=host.docker.internal:host-gateway \
    -e ES_URL -e ES_API_KEY -e ES_USERNAME -e ES_PASSWORD -e ES_SSL_SKIP_VERIFY \
    "$ES_MCP_IMAGE" http > /dev/null
}

start_dynatrace() {
  echo "[dynatrace] 啟動 Dynatrace 唯讀 MCP 於 :$DT_MCP_PORT，DT_BASE_URL=$DT_BASE_URL"
  (cd "$AGENT_DIR" && DT_MCP_PORT="$DT_MCP_PORT" nohup uv run python -m dynatrace_mcp.server > "$LOG_DIR/dynatrace-mcp.log" 2>&1 &
   echo $! > "$RUN_DIR/dynatrace-mcp.pid")
}

stop_pid() {
  local name="$1" pidfile="$RUN_DIR/$1.pid"
  if [[ -f "$pidfile" ]]; then
    local pid; pid="$(cat "$pidfile")"
    # 連同 npx / uv 產生的子程序一起停止
    pkill -P "$pid" > /dev/null 2>&1 || true
    kill "$pid" > /dev/null 2>&1 || true
    rm -f "$pidfile"
    echo "[$name] 已停止"
  fi
}

check() {
  local name="$1" url="$2"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$url" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' --max-time 5 || true)"
  if [[ "$code" =~ ^(200|202|400|401|406)$ ]]; then
    echo "[$name] 可連線 $url（HTTP $code）"
  else
    echo "[$name] 無法連線 $url（HTTP ${code:-000}）"
  fi
}

cmd="${1:-}"
mkdir -p "$LOG_DIR" "$RUN_DIR"

case "$cmd" in
  start)
    start_k8s
    start_prometheus
    if is_true "${ENABLE_ELASTICSEARCH:-false}"; then start_elasticsearch; else echo "[elasticsearch] 未啟用，略過"; fi
    if is_true "${ENABLE_DYNATRACE:-false}"; then start_dynatrace; else echo "[dynatrace] 未啟用，略過"; fi
    echo "完成。日誌：$LOG_DIR；檢查狀態：scripts/mcp.sh status"
    ;;
  stop)
    stop_pid k8s-mcp
    stop_pid dynatrace-mcp
    docker rm -f patrol-prometheus-mcp > /dev/null 2>&1 && echo "[prometheus] 已停止" || true
    docker rm -f patrol-elasticsearch-mcp > /dev/null 2>&1 && echo "[elasticsearch] 已停止" || true
    ;;
  status)
    check kubernetes "${K8S_MCP_URL:-http://localhost:$K8S_MCP_PORT/mcp}"
    check prometheus "${PROM_MCP_URL:-http://localhost:$PROM_MCP_PORT/mcp}"
    if is_true "${ENABLE_ELASTICSEARCH:-false}"; then check elasticsearch "${ES_MCP_URL:-http://localhost:$ES_MCP_PORT/mcp}"; fi
    if is_true "${ENABLE_DYNATRACE:-false}"; then check dynatrace "${DT_MCP_URL:-http://localhost:$DT_MCP_PORT/mcp}"; fi
    ;;
  *)
    echo "用法：$0 {start|stop|status}" >&2
    exit 1
    ;;
esac
