# SRE Patrol Agent

[English](README_EN.md)

給定一個 Kubernetes namespace，自動巡檢底下所有 Deployment，產出巡檢報告的 AI Agent。

- 以 [deepagents](https://github.com/langchain-ai/deepagents) 0.7.39 建立主協調 Agent 與子 Agent
- 透過 MCP 串接既有的可觀測性工具：**Kubernetes、Prometheus**（固定啟用），**Elasticsearch、Dynatrace**（選用，預設關閉）
- 由模型自主判斷每個 Deployment 的健康狀態（OK / WARN / CRIT / UNKNOWN），每個結論都必須附上證據
- 全程唯讀：不會修改叢集或任何資料
- 可排程定時巡檢，也可手動觸發；報告寫成 Markdown 檔

架構圖與流程圖見 [docs/architecture.md](docs/architecture.md)。

## 元件

| 層 | 元件 | 說明 |
| --- | --- | --- |
| 觸發 | `patrol_agent/patrol.py` | 單次或定時巡檢，報告寫到 `reports/` |
| 觸發 | `deep-agents-ui/` | 聊天介面，可追問或手動巡檢（:3300） |
| Agent | 主巡檢 Agent | 列出 Deployment → 分批委派 → 綜合判斷 → 寫報告 |
| Agent | `k8s-agent` | Deployment / Pod 狀態、重啟、OOM、Warning 事件、崩潰前日誌 |
| Agent | `prometheus-agent` | 資源用量、CPU 節流、記憶體佔 limit、錯誤率與延遲（與基準比較） |
| Agent | `log-agent`（選用） | Elasticsearch 錯誤日誌統計（先確認 index 與欄位） |
| Agent | `dynatrace-agent`（選用） | open problem、服務指標、近期部署與變更事件 |
| MCP | [kubernetes-mcp-server](https://github.com/containers/kubernetes-mcp-server) | 唯讀模式，npx 執行 |
| MCP | [prometheus-mcp-server](https://github.com/pab1it0/prometheus-mcp-server) | Docker 執行 |
| MCP | [Elastic mcp-server-elasticsearch](https://github.com/elastic/mcp-server-elasticsearch) | Docker 執行（選用） |
| MCP | `patrol_agent/dynatrace_mcp/` | 本 repo 內建，Dynatrace API v2 唯讀（選用） |

## 需求

- Python 3.11+ 與 [uv](https://docs.astral.sh/uv/)
- Node.js 20+（kubernetes-mcp-server 與 UI）
- Docker（Prometheus / Elasticsearch MCP）
- 一份**唯讀**的 kubeconfig（建議只授予 get / list / watch 權限）
- LLM API key（OpenAI、Anthropic 或 OpenAI 相容端點）

## 快速開始

### 1. 設定

```bash
cp patrol_agent/.env.example patrol_agent/.env
```

至少填寫：

- `PATROL_MODEL`（例如 `openai:gpt-5.2`、`anthropic:claude-sonnet-5-5`）與對應的 API key
- 使用 OpenAI 相容端點時填 `OPENAI_BASE_URL`
- `PROMETHEUS_URL`（MCP 跑在 Docker 裡，Prometheus 在本機時用 `http://host.docker.internal:9090`）
- `K8S_MCP_KUBECONFIG`（留空則用 `$KUBECONFIG` 或 `~/.kube/config`）

要啟用 Elasticsearch / Dynatrace 時，把 `ENABLE_ELASTICSEARCH` / `ENABLE_DYNATRACE` 設為 `true`，並填寫對應的連線設定。

### 2. 安裝相依套件

```bash
cd patrol_agent && uv sync
```

### 3. 啟動 MCP server

```bash
scripts/mcp.sh start
```

```bash
scripts/mcp.sh status
```

### 4. 啟動 Agent

```bash
cd patrol_agent && uv run langgraph dev --no-browser
```

### 5. 執行巡檢

單次巡檢：

```bash
cd patrol_agent && uv run python patrol.py --namespace payments --once
```

每 30 分鐘巡檢一次（可重複 `--namespace` 巡檢多個）：

```bash
cd patrol_agent && uv run python patrol.py --namespace payments --namespace orders --interval 30m
```

不啟動 `langgraph dev`，直接在本機程序內執行：

```bash
cd patrol_agent && uv run python patrol.py --namespace payments --once --local
```

報告會寫到 `reports/<namespace>/<YYYYmmdd-HHMMSS>.md`。

### 6.（選用）聊天介面

```bash
cd deep-agents-ui && yarn install && yarn dev
```

打開 http://localhost:3300，Deployment URL 填 `http://localhost:2024`，Assistant ID 填 `patrol_agent`。
透過 `patrol.py`（非 `--local`）執行的巡檢也會出現在 UI 的 thread 列表。

## 主要設定

| 環境變數 | 預設 | 說明 |
| --- | --- | --- |
| `PATROL_MODEL` | `openai:gpt-5.2` | 模型，`provider:model` 格式 |
| `ENABLE_ELASTICSEARCH` | `false` | 啟用 log-agent 與 Elasticsearch MCP |
| `ENABLE_DYNATRACE` | `false` | 啟用 dynatrace-agent 與 Dynatrace MCP |
| `PATROL_BATCH_SIZE` | `3` | 每批平行巡檢的 Deployment 數 |
| `PATROL_MAX_ROUNDS_PER_DEPLOYMENT` | `2` | 每個 Deployment 最多委派幾輪 |
| `PATROL_RECURSION_LIMIT` | `300` | 整次巡檢的步數上限 |
| `PATROL_LOOKBACK` | `1h` | 預設查詢時間窗 |
| `MCP_MAX_RESULT_CHARS` | `20000` | 單次工具結果上限，超過會截斷 |

完整清單見 [patrol_agent/.env.example](patrol_agent/.env.example)。開關與設定在 Agent 啟動時讀取，修改後要重啟 `langgraph dev`。

## 報告範例結構

```
# Kubernetes 巡檢報告：payments
- 巡檢時間 / 時間窗 / 資料來源啟用狀態 / 結果摘要
## 總表          每個 Deployment 一行：狀態、各來源狀態、一句話摘要
## 異常詳情      CRIT / WARN 的問題、證據、可能原因、建議
## 證據清單      [1] 來源 | 對象 | 查詢 | 數值 | 時間
```

## 開發

```bash
cd patrol_agent && uv run pytest
```

測試涵蓋：開關對子 Agent 與 prompt 的影響、移除 general-purpose、工具白名單、Dynatrace API 客戶端、報告擷取。

## 注意事項

- **Elastic 官方 MCP 已標示 deprecated**（只提供安全性更新），官方建議 Elastic 9.2+ 改用 Agent Builder MCP 端點。它支援 Elasticsearch 8.x / 9.x。工具名稱可用 `ES_MCP_TOOL_*` 環境變數覆寫，方便日後改接。
- **Dynatrace MCP 是本 repo 自寫**，針對 SaaS 舊版與 Managed 的 Environment API v2。Dynatrace 新平台（`*.apps.dynatrace.com`）可改用官方 [dynatrace-mcp](https://github.com/dynatrace-oss/dynatrace-mcp)。
- 模型判斷有不確定性；報告中的推論應由人員確認後再採取行動。

## 授權

MIT。本專案衍生自 [SRE-Agent](https://github.com/vapering/SRE-Agent)，原始著作權聲明保留於 [LICENSE](LICENSE)；`deep-agents-ui/` 來自 LangChain 的 deep-agents-ui（MIT，見 [deep-agents-ui/LICENSE](deep-agents-ui/LICENSE)）。
