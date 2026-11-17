# 架構與運作流程

## 架構圖

虛線為選用元件，預設關閉，用 `ENABLE_ELASTICSEARCH` / `ENABLE_DYNATRACE` 開啟。

```mermaid
graph TD
    User((SRE / 使用者))

    subgraph Trigger["⏰ 觸發層"]
        CLI["patrol.py<br/>--namespace xxx --once<br/>--namespace xxx --interval 30m<br/>--local（不經 API）"]
        UI["deep-agents-ui<br/>Next.js :3300"]
    end

    subgraph AgentLayer["🤖 Agent 層（langgraph dev :2024 · deepagents 0.7.39）"]
        Main["主巡檢 Agent（patrol_agent）<br/>模型：PATROL_MODEL<br/>無 general-purpose、排除 execute"]
        K8sA["k8s-agent"]
        PromA["prometheus-agent"]
        LogA["log-agent（Elasticsearch）"]
        DtA["dynatrace-agent"]
    end

    subgraph MCP["🔌 MCP 轉接層（本機，scripts/mcp.sh）"]
        K8sMCP["Kubernetes MCP :18080<br/>kubernetes-mcp-server · read_only"]
        PromMCP["Prometheus MCP :18090<br/>prometheus-mcp-server（Docker）"]
        ESMCP["Elasticsearch MCP :18100<br/>Elastic 官方（Docker）"]
        DtMCP["Dynatrace MCP :18110<br/>repo 內 dynatrace_mcp · API v2"]
    end

    subgraph Existing["🏢 既有環境"]
        K8s[("Kubernetes 叢集")]
        Prom[("Prometheus")]
        ES[("Elasticsearch")]
        DT[("Dynatrace<br/>SaaS 舊版 / Managed")]
    end

    Reports[["reports/&lt;namespace&gt;/&lt;時間&gt;.md"]]

    User --> CLI
    User --> UI
    CLI -->|"LangGraph API"| Main
    UI -->|"LangGraph SDK"| Main
    CLI -->|"寫入報告"| Reports

    Main -->|"task()"| K8sA
    Main -->|"task()"| PromA
    Main -.->|"task()"| LogA
    Main -.->|"task()"| DtA

    K8sA --> K8sMCP --> K8s
    PromA --> PromMCP --> Prom
    LogA -.-> ESMCP -.-> ES
    DtA -.-> DtMCP -.-> DT

    classDef optional stroke-dasharray: 5 5,opacity:0.75
    class LogA,DtA,ESMCP,DtMCP,ES,DT optional
```

## 一次巡檢的運作流程

```mermaid
sequenceDiagram
    autonumber
    participant T as patrol.py / UI
    participant M as 主巡檢 Agent
    participant K as k8s-agent
    participant P as prometheus-agent
    participant L as log-agent（選用）
    participant D as dynatrace-agent（選用）
    participant F as reports/

    T->>M: 巡檢 namespace=xxx（含巡檢時間、時間窗）
    M->>M: write_todos：取得清單 → 分批巡檢 → 撰寫報告
    M->>K: 列出所有 Deployment 與基本狀態
    K-->>M: 副本數 / selector / 映像 / 重啟 / Warning 事件
    M->>M: 每個 Deployment 一個待辦，每批 PATROL_BATCH_SIZE 個

    loop 每一批
        par 平行委派
            M->>K: Pod / rollout / 事件 / 崩潰前日誌
            K-->>M: 狀態 + 證據
        and
            M->>P: 先探索指標，再查副本、重啟、OOM、記憶體、CPU 節流、錯誤率
            P-->>M: 狀態 + PromQL + 數值
        and 若 ENABLE_ELASTICSEARCH
            M->>L: 先查 index 與 mapping，再統計錯誤日誌
            L-->>M: 狀態 + 查詢 + 代表性日誌
        and 若 ENABLE_DYNATRACE
            M->>D: 對應實體、open problem、服務指標、近期事件
            D-->>M: 狀態 + API 查詢 + 數值
        end
        M->>M: sre_think_tool：交叉比對、暫定狀態、是否追查（每個 Deployment 最多 N 輪）
    end

    alt 接近步數上限
        M->>M: 停止委派，未完成者標記「未巡檢」
    end

    M->>M: write_file /patrol_report.md
    M-->>T: 最終回覆
    T->>F: 寫入 reports/xxx/YYYYmmdd-HHMMSS.md
```

## 開關的影響範圍

| 範圍 | `ENABLE_ELASTICSEARCH=false` / `ENABLE_DYNATRACE=false`（預設） |
| --- | --- |
| 子 Agent | 不註冊 log-agent / dynatrace-agent |
| 工具 | 不 import 對應模組，不連線 MCP |
| 主 Agent prompt | 可用子 Agent、派工步驟、判斷標準、報告欄位都不包含該來源 |
| `scripts/mcp.sh` | 不啟動對應 MCP server |
| 報告 | 開頭標註「未啟用」 |

開關在 Agent 啟動時讀取，修改後要重啟 `langgraph dev`。

## 安全設計

- **三層唯讀**：
  1. MCP server 本身以唯讀模式啟動（kubernetes-mcp-server `read_only = true`；Dynatrace MCP 只有 GET）。
  2. Agent 端的 MCP 客戶端有工具白名單（`mcp_client.MCPClient.allowed_tools`），白名單外的工具一律拒絕。
  3. prompt 明確禁止任何修改動作。
- **移除不需要的能力**：透過 deepagents harness profile 移除 general-purpose 子 Agent，並排除 `execute` 工具。
- **步數上限**：`PATROL_RECURSION_LIMIT`；每批數量 `PATROL_BATCH_SIZE`；每個 Deployment 委派輪數 `PATROL_MAX_ROUNDS_PER_DEPLOYMENT`。
- **結果截斷**：單次工具結果超過 `MCP_MAX_RESULT_CHARS` 會截斷並提示模型縮小查詢範圍。
