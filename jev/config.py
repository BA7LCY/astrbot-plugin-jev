"""配置定义与启动校验。"""

from dataclasses import dataclass, fields
from math import isfinite


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    group_enabled: bool = True
    private_enabled: bool = False
    pre_check_enabled: bool = True
    post_check_enabled: bool = True
    recall_enabled: bool = True
    history_enabled: bool = True
    context_enabled: bool = True
    bypass_commands: bool = True
    fail_open: bool = False
    api_key: str = ""
    model: str = "jev-latest"
    timeout_seconds: float = 8.0
    threshold: float = 0.65
    context_messages: int = 20
    max_sessions: int = 128
    text_limit: int = 2000
    history_limit: int = 1000

    @classmethod
    def load(cls, values: dict) -> "Settings":
        """加载并验证设置，不静默修正错误配置。

        Args:
            values: 宿主传入的配置。

        Returns:
            已验证的设置。

        Raises:
            ValueError: 配置类型、范围或鉴权设置不合法。
        """
        defaults = cls()
        data = {}
        for field in fields(cls):
            default = getattr(defaults, field.name)
            value = values.get(field.name, default)
            if isinstance(default, bool):
                valid = type(value) is bool
            elif isinstance(default, int):
                valid = type(value) is int
            elif isinstance(default, float):
                valid = type(value) in (int, float) and isfinite(value)
            else:
                valid = isinstance(value, str)
            if not valid:
                raise ValueError(f"Invalid setting: {field.name}")
            data[field.name] = value
        result = cls(**data)
        ranges = {
            "timeout_seconds": (0.1, 60),
            "threshold": (0, 1),
            "context_messages": (1, 50),
            "max_sessions": (1, 1000),
            "text_limit": (100, 4000),
            "history_limit": (1, 10000),
        }
        for name, (low, high) in ranges.items():
            if not low <= getattr(result, name) <= high:
                raise ValueError(f"Out-of-range setting: {name}")
        if not result.model.strip():
            raise ValueError("Empty model")
        return result
