"""巡檢用的結構化思考工具。"""

from langchain_core.tools import tool


@tool(parse_docstring=True)
def sre_think_tool(patrol_log: str) -> str:
    """巡檢過程中做結構化整理的思考工具，不會查詢任何資料。

    每批 Deployment 的子 Agent 回報後使用，整理已知資訊並決定下一步。

    內容應包含：
    1. 已確認的事實：各資料來源回報了哪些有證據的異常（具體數值與時間）
    2. 交叉比對：不同來源的發現是否互相印證或矛盾
    3. 暫定狀態：每個 Deployment 目前判斷為 OK / WARN / CRIT / UNKNOWN 及理由
    4. 缺口：哪些 Deployment 或資料來源還沒有結果、是否值得追加委派（注意輪數上限）
    5. 下一步：要巡檢的下一批，或開始撰寫報告

    Args:
        patrol_log: 巡檢整理內容，需包含事實、比對、暫定狀態、缺口與下一步。

    Returns:
        確認整理內容已記錄。
    """
    return f"巡檢整理已記錄：{patrol_log}"
