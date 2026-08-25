"""Brain 层：Agent 的"大脑"，负责测试规划与失败分析。

- MockBrain：规则引擎，离线可跑，保证演示确定性
- LLMBrain：LangChain + OpenAI 兼容接口，结构化输出规划/分析
"""
from .base import BaseBrain
from .mock_brain import MockBrain
from .llm_brain import LLMBrain

__all__ = ["BaseBrain", "MockBrain", "LLMBrain"]
