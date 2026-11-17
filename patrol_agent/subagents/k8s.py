"""k8s-agent：透過 Kubernetes MCP（唯讀）檢查 Deployment / Pod / 事件。"""

from __future__ import annotations

from langchain_core.tools import tool

from mcp_client import MCPClient
from settings import settings

client = MCPClient(
    name="kubernetes",
    url=settings.k8s_mcp_url,
    allowed_tools={
        "namespaces_list",
        "resources_list",
        "resources_get",
        "pods_list_in_namespace",
        "pods_get",
        "pods_log",
        "pods_top",
        "events_list",
    },
)


@tool
async def k8s_list_resources(
    kind: str,
    namespace: str,
    api_version: str = "apps/v1",
    label_selector: str | None = None,
    field_selector: str | None = None,
    limit: int | None = None,
) -> str:
    """列出 namespace 內某種資源，例如 Deployment、ReplicaSet、HorizontalPodAutoscaler。

    Args:
        kind: 資源種類，例如 Deployment、ReplicaSet、StatefulSet、HorizontalPodAutoscaler。
        namespace: 目標 namespace。
        api_version: API 版本，Deployment/ReplicaSet 用 apps/v1，HPA 用 autoscaling/v2。
        label_selector: 標籤篩選，例如 app=checkout。
        field_selector: 欄位篩選。
        limit: 最多回傳幾筆。
    """
    return await client.call(
        "resources_list",
        {
            "apiVersion": api_version,
            "kind": kind,
            "namespace": namespace,
            "labelSelector": label_selector,
            "fieldSelector": field_selector,
            "limit": limit,
        },
    )


@tool
async def k8s_get_resource(kind: str, name: str, namespace: str, api_version: str = "apps/v1") -> str:
    """取得單一資源的完整內容（含 spec、status、conditions）。

    Args:
        kind: 資源種類，例如 Deployment。
        name: 資源名稱。
        namespace: 所在 namespace。
        api_version: API 版本，Deployment 用 apps/v1。
    """
    return await client.call(
        "resources_get",
        {"apiVersion": api_version, "kind": kind, "name": name, "namespace": namespace},
    )


@tool
async def k8s_list_pods(
    namespace: str,
    label_selector: str | None = None,
    field_selector: str | None = None,
) -> str:
    """列出 namespace 內的 Pod，可用 Deployment 的 selector 標籤篩選。

    Args:
        namespace: 目標 namespace。
        label_selector: 標籤篩選，通常用 Deployment spec.selector.matchLabels，例如 app=checkout。
        field_selector: 欄位篩選，例如 status.phase!=Running。
    """
    return await client.call(
        "pods_list_in_namespace",
        {"namespace": namespace, "labelSelector": label_selector, "fieldSelector": field_selector},
    )


@tool
async def k8s_get_pod(name: str, namespace: str) -> str:
    """取得單一 Pod 的完整內容（containerStatuses、restartCount、lastState 等）。

    Args:
        name: Pod 名稱。
        namespace: 所在 namespace。
    """
    return await client.call("pods_get", {"name": name, "namespace": namespace})


@tool
async def k8s_pod_logs(
    name: str,
    namespace: str,
    container: str | None = None,
    previous: bool = False,
    tail: int = 100,
) -> str:
    """讀取 Pod 容器日誌。容器重啟過時用 previous=true 看上一次崩潰前的日誌。

    Args:
        name: Pod 名稱。
        namespace: 所在 namespace。
        container: 容器名稱；多容器 Pod 必填。
        previous: 是否讀取上一個（已終止）容器實例的日誌。
        tail: 只讀最後幾行，預設 100。
    """
    return await client.call(
        "pods_log",
        {"name": name, "namespace": namespace, "container": container, "previous": previous, "tail": tail},
    )


@tool
async def k8s_pods_top(namespace: str, label_selector: str | None = None) -> str:
    """查看 Pod 目前的 CPU / 記憶體用量（需要叢集有 metrics-server）。

    Args:
        namespace: 目標 namespace。
        label_selector: 標籤篩選，例如 app=checkout。
    """
    return await client.call("pods_top", {"namespace": namespace, "label_selector": label_selector})


@tool
async def k8s_list_events(namespace: str, field_selector: str | None = None) -> str:
    """列出 namespace 內的事件。巡檢時通常只看 Warning：field_selector="type=Warning"。

    Args:
        namespace: 目標 namespace。
        field_selector: 欄位篩選，例如 type=Warning 或 involvedObject.name=<pod 名稱>。
    """
    return await client.call("events_list", {"namespace": namespace, "fieldSelector": field_selector})


TOOLS = [
    k8s_list_resources,
    k8s_get_resource,
    k8s_list_pods,
    k8s_get_pod,
    k8s_pod_logs,
    k8s_pods_top,
    k8s_list_events,
]

DESCRIPTION = (
    "Kubernetes 巡檢專家（唯讀）。用來列出 namespace 下的 Deployment，以及檢查 Deployment、"
    "ReplicaSet、Pod 的狀態：副本是否到位、rollout 是否卡住、Pod 重啟與 OOMKilled、"
    "Warning 事件、容器崩潰前日誌。"
)

PROMPT = """你是 Kubernetes 巡檢專家，只能使用唯讀工具檢查叢集狀態，絕對不能嘗試修改任何資源。

<任務>
依照主 Agent 的指示，檢查指定 namespace 或指定 Deployment 的健康狀態，回報有證據的發現。
</任務>

<可用工具>
- k8s_list_resources：列出 Deployment / ReplicaSet / HPA 等資源
- k8s_get_resource：取得單一資源完整內容
- k8s_list_pods：用 Deployment 的 selector 標籤列出 Pod
- k8s_get_pod：取得 Pod 詳細狀態
- k8s_pod_logs：讀取容器日誌（previous=true 讀崩潰前日誌）
- k8s_pods_top：Pod 目前 CPU / 記憶體用量
- k8s_list_events：namespace 事件（巡檢時用 type=Warning）
</可用工具>

<檢查要點>
1. Deployment 對應關係：Deployment → ReplicaSet → Pod。用 Deployment 的 spec.selector.matchLabels 組成 label_selector 找 Pod，不要用名稱前綴猜。
2. 副本：比對 spec.replicas、status.readyReplicas、status.availableReplicas、status.updatedReplicas、status.unavailableReplicas。
3. Rollout：看 status.conditions（Available、Progressing），注意 ProgressDeadlineExceeded；observedGeneration 落後 metadata.generation 代表控制器還沒處理最新版本。
4. Pod：phase 不是 Running、containerStatuses 的 ready=false、restartCount 偏高、state.waiting.reason（CrashLoopBackOff、ImagePullBackOff、CreateContainerConfigError）、lastState.terminated.reason（OOMKilled、Error）及 exitCode。
5. 事件：只看 type=Warning，例如 FailedScheduling、BackOff、Unhealthy（探針失敗）、FailedMount、Evicted。注意事件的 count 與最後發生時間。
6. 有重啟或崩潰時，用 k8s_pod_logs 搭配 previous=true 讀最後幾十行，找出崩潰原因。
7. 資源設定：容器沒有設定 resources.requests / limits 時記為 WARN 風險。

列出清單時，請精簡回報每個 Deployment 的：名稱、期望/就緒/可用副本數、selector 標籤、目前映像版本、Pod 總重啟次數，不要貼完整 YAML。
</檢查要點>

<回報格式>
每個 Deployment 一段，使用以下格式（沒有異常也要回報 OK）：

deployment: <名稱> | 資料來源: kubernetes | 狀態: OK / WARN / CRIT
發現: <一到三句重點>
證據: <具體欄位與數值，例如 readyReplicas=1/3、restartCount=17、reason=OOMKilled、事件 BackOff x42（最後發生 2026-11-17T08:12:00Z）>
建議: <可執行的下一步；狀態 OK 時寫「無」>

狀態判斷原則：
- CRIT：可用副本為 0、多數 Pod 處於 CrashLoopBackOff / ImagePullBackOff、rollout 失敗（ProgressDeadlineExceeded）
- WARN：部分副本未就緒、近期有重啟或 OOMKilled、持續的 Warning 事件、缺少 resources 設定
- OK：副本全部就緒、無重啟、無 Warning 事件

沒有查到證據的事情不要寫成發現。工具回傳錯誤時，明確寫出「無法取得」及原因。
使用繁體中文，簡潔明瞭。
</回報格式>
"""


def build_subagent(model: str) -> dict:
    """建立 deepagents 子 Agent 規格。"""
    return {
        "name": "k8s-agent",
        "description": DESCRIPTION,
        "system_prompt": PROMPT,
        "tools": TOOLS,
        "model": model,
    }
