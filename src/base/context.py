"""基础上下文管理 - 简单消息列表"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class BaseContext:
    """基础上下文管理器 - 仅维护消息列表，无三区模型"""

    messages: List[dict] = None

    def __post_init__(self):
        if self.messages is None:
            self.messages = []

    def init_with_system(self, system_prompt: str):
        """初始化上下文（设置 system 消息）"""
        self.messages = [{"role": "system", "content": system_prompt}]

    def append(self, message: dict):
        """追加消息"""
        self.messages.append(message)

    def extend(self, messages: List[dict]):
        """批量追加消息"""
        self.messages.extend(messages)

    def pop(self) -> Optional[dict]:
        """弹出最后一条消息"""
        if self.messages:
            return self.messages.pop()
        return None

    def clear_natural(self):
        """清空非 system 消息"""
        if self.messages and self.messages[0]["role"] == "system":
            self.messages = self.messages[:1]
        else:
            self.messages = []

    def __len__(self) -> int:
        return len(self.messages)

    def __getitem__(self, idx):
        return self.messages[idx]
