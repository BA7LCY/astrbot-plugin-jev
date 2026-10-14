"""配置定义与启动校验。"""

import ipaddress
from dataclasses import dataclass, field, fields
from math import isfinite
from urllib.parse import urlsplit

DEFAULT_API_BASE = "https://api.typesafe.ai"
INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".arpa")


def normalize_api_base(value: str) -> str:
    """校验并规范化判断接口基址。

    Args:
        value: 配置里的基址，可以带路径前缀，如 ``https://ai-gateway.vercel.sh/typesafe``。

    Returns:
        去掉末尾斜杠后的基址。

    Raises:
        ValueError: 不是 https 基址、含凭据或查询串，或指向内网地址。
    """
    base = value.strip().rstrip("/")
    parts = urlsplit(base)
    host = parts.hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        internal = host == "localhost" or host.endswith(INTERNAL_SUFFIXES)
    else:
        internal = (
            address.is_private
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        )
    if (
        parts.scheme != "https"
        or not host
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
        or ".." in parts.path
        or internal
    ):
        raise ValueError("Invalid setting: api_base")
    return base


STYLE_ALIASES = {
    "quiet": "quiet", "balanced": "balanced", "sociable": "sociable",
    "安静": "quiet", "平衡": "balanced", "活泼": "sociable",
}
SCOPE_ALIASES = {
    "group": "group", "sender": "sender", "global": "global",
    "同群": "group", "同人": "sender", "全局": "global",
}


def alias(value, table: dict, name: str) -> str:
    """把面板上的中文选项归一成内部英文码；非法值直接报错。"""
    if not isinstance(value, str) or value.strip() not in table:
        raise ValueError(f"Invalid setting: {name}")
    return table[value.strip()]


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    group_enabled: bool = False
    private_enabled: bool = False
    pre_check_enabled: bool = True
    post_check_enabled: bool = True
    recall_enabled: bool = True
    gates_follow_style: bool = True

    burst_merge_enabled: bool = False
    dry_run_enabled: bool = False
    history_enabled: bool = True
    bypass_commands: bool = True
    bypass_addressed: bool = False
    bypass_addressed_whitelist: list[str] = field(default_factory=list)
    busy_lock_enabled: bool = True
    busy_lock_scope: str = "同群"
    reply_style: str = "平衡"
    burst_window_seconds: float = 1.5
    reply_cooldown_seconds: float = 0.0
    intrusive_block_threshold: float = 0.45
    join_intrusion_ceiling: float = 0.20
    continuation_threshold: float = 0.70
    addressed_threshold: float = 0.80
    worthwhile_min: float = 0.50
    review_appropriate_min: float = 0.75
    review_redundant_max: float = 0.35
    review_freshness_min: float = 0.5
    busy_lock_timeout_seconds: int = 180
    fail_open: bool = False
    api_key: str = ""
    api_base: str = DEFAULT_API_BASE
    model: str = "jev-latest"
    timeout_seconds: float = 8.0

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
            elif isinstance(default, list):
                valid = isinstance(value, list) and all(
                    isinstance(item, (str, int)) for item in value
                )
            else:
                valid = isinstance(value, str)
            if not valid:
                raise ValueError(f"Invalid setting: {field.name}")
            if isinstance(value, list):
                value = sorted(
                    {str(item).strip() for item in value if str(item).strip()}
                )
            data[field.name] = value
        data["api_base"] = normalize_api_base(data["api_base"])
        data["busy_lock_scope"] = alias(data["busy_lock_scope"], SCOPE_ALIASES, "busy_lock_scope")
        data["reply_style"] = alias(data["reply_style"], STYLE_ALIASES, "reply_style")
        result = cls(**data)
        ranges = {
            "timeout_seconds": (0.1, 60),

            "text_limit": (100, 4000),
            "intrusive_block_threshold": (0, 1),
            "join_intrusion_ceiling": (0, 1),
            "continuation_threshold": (0, 1),
            "addressed_threshold": (0, 1),
            "worthwhile_min": (0, 1),
            "review_appropriate_min": (0, 1),
            "review_redundant_max": (0, 1),
            "review_freshness_min": (0, 1),
            "burst_window_seconds": (0.2, 10),
            "reply_cooldown_seconds": (0, 600),
            "history_limit": (1, 10000),
            "busy_lock_timeout_seconds": (1, 86400),
        }
        for name, (low, high) in ranges.items():
            if not low <= getattr(result, name) <= high:
                raise ValueError(f"Out-of-range setting: {name}")
        if not result.model.strip():
            raise ValueError("Empty model")
        return result
