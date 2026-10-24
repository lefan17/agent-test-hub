"""构建 LangGraph 状态图。

流程：

    planner ──> executor ──(条件边)──> critic ──> reporter ──> END
                   ↑            │
                   └── retrier ┘
                        │
                        └──(无剩余可重试失败 / 轮次用完)──> critic

`retrier` 是一条**真实的回边**，不是装饰：executor 跑完后由条件边判断是否还有
「重试预算内、且失败原因是 5xx/无响应」的用例，有就回到 executor 补跑一轮
（只补跑失败的那几条，不重跑全量），没有就前进到 critic。

为什么需要它：executor 内部的 max_attempts 重试是**同一次执行里的连续退避**
（0.3s/0.6s/1.2s），对「服务端偶发 503」这类问题常常在同一次抖动窗口里连续失败；
而跨轮补跑把时间窗口拉开了，`/api/flaky` 这种概率性失败才有机会在第二轮通过，
从而不被误判成缺陷。轮次上限由 `Config.retry_rounds` 控制（默认 1，即最多补跑一轮），
既给概率性失败留了机会，又不会无限重试——重试用尽的失败照常进 critic 归因。
"""

from __future__ import annotations

from typing import Any, Dict, List

from langgraph.graph import END, StateGraph

from agenttest.brain import BaseBrain
from agenttest.config import Config
from agenttest.graph.nodes import make_critic, make_executor, make_planner, make_reporter
from agenttest.graph.state import AgentState
from agenttest.models import TestResult

# 「值得跨轮补跑」的失败：服务端 5xx 或压根没拿到响应。
# 业务 4xx 不在此列——那是被测系统的确定性行为，重跑一百遍也是 4xx，
# 把它们拉进来只会拖慢流水线并制造「重试掩盖缺陷」的嫌疑。
RETRYABLE_STATUSES = (0, 500, 502, 503, 504)


def _retry_candidates(results: List[TestResult]) -> List[TestResult]:
    """筛出本可以补跑的失败结果。

    `invalid_case=True` 的必须排除：那表示失败原因是**用例自己写错了**
    （例如把 RAG 断言写进 API 用例），补跑不会让它变绿，只会白烧一轮。
    """
    return [r for r in results if not r.ok and not r.invalid_case and r.actual_status in RETRYABLE_STATUSES]


def make_retrier():
    def retrier(state: Dict[str, Any]) -> Dict[str, Any]:
        ctx = state["context"]
        used = int(ctx.get("retry_round", 0)) + 1
        ids = [r.case_id for r in _retry_candidates(state["results"])]
        ctx["retry_round"] = used
        # executor 读这个键：非空表示本轮只补跑这些用例，跑完清空。
        ctx["retry_case_ids"] = ids
        return {
            "trace": [f"[retrier] 第 {used} 轮补跑：{len(ids)} 条失败（5xx/无响应）可重试 {ids}；其余失败直接进归因"]
        }

    return retrier


def _route_after_executor(cfg: Config):
    """executor 之后的条件边：回 retrier 补跑，还是前进到 critic。"""

    def route(state: Dict[str, Any]) -> str:
        ctx = state["context"]
        used = int(ctx.get("retry_round", 0))
        if used >= cfg.retry_rounds:
            return "critic"
        if not _retry_candidates(state["results"]):
            return "critic"
        return "retrier"

    return route


def build_graph(brain: BaseBrain, cfg: Config):
    """planner -> executor -(条件)-> (retrier -> executor)* -> critic -> reporter

    每个节点都是纯函数（输入 state 输出 state 增量），可单独测试、可替换、可流式观测。
    图里有一处条件分支（executor 之后）与一条回边（retrier -> executor），
    这两件事是真的在本文件里声明的，不是文档里的说法。
    """
    graph = StateGraph(AgentState)
    graph.add_node("planner", make_planner(brain, cfg))
    graph.add_node("executor", make_executor(cfg))
    graph.add_node("retrier", make_retrier())
    graph.add_node("critic", make_critic(brain))
    graph.add_node("reporter", make_reporter(brain, cfg))
    graph.set_entry_point("planner")
    graph.add_edge("planner", "executor")
    # 条件边：还有可重试失败且轮次没用完 → retrier 补跑一轮；否则直接归因。
    graph.add_conditional_edges(
        "executor",
        _route_after_executor(cfg),
        {"retrier": "retrier", "critic": "critic"},
    )
    graph.add_edge("retrier", "executor")
    graph.add_edge("critic", "reporter")
    graph.add_edge("reporter", END)
    return graph.compile()
