"""构建 LangGraph 状态图。"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from agenttest.brain import BaseBrain
from agenttest.config import Config
from agenttest.graph.nodes import make_critic, make_executor, make_planner, make_reporter
from agenttest.graph.state import AgentState


def build_graph(brain: BaseBrain, cfg: Config):
    """planner -> executor -> critic -> reporter

    每个节点都是纯函数（输入 state 输出 state 增量），可单独测试、可替换、可流式观测，
    这就是用 LangGraph 做流程编排的价值：测试策略与执行逻辑解耦。
    """
    graph = StateGraph(AgentState)
    graph.add_node("planner", make_planner(brain, cfg))
    graph.add_node("executor", make_executor(cfg))
    graph.add_node("critic", make_critic(brain))
    graph.add_node("reporter", make_reporter(brain, cfg))
    graph.set_entry_point("planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "critic")
    graph.add_edge("critic", "reporter")
    graph.add_edge("reporter", END)
    return graph.compile()
