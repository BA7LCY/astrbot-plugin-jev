"""配置定义与启动校验。"""

import ipaddress
from dataclasses import dataclass, fields
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


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    group_enabled: bool = False
    private_enabled: bool = False
    pre_check_enabled: bool = True
    post_check_enabled: bool = True
    recall_enabled: bool = True
    history_enabled: bool = True
    bypass_commands: bool = True
    fail_open: bool = False
    api_key: str = ""
    api_base: str = DEFAULT_API_BASE
    model: str = "jev-latest"
    timeout_seconds: float = 8.0
    threshold: float = 0.5
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
        data["api_base"] = normalize_api_base(data["api_base"])
        result = cls(**data)
        ranges = {
            "timeout_seconds": (0.1, 60),
            "threshold": (0, 1),
            "text_limit": (100, 4000),
            "history_limit": (1, 10000),
        }
        for name, (low, high) in ranges.items():
            if not low <= getattr(result, name) <= high:
                raise ValueError(f"Out-of-range setting: {name}")
        if not result.model.strip():
            raise ValueError("Empty model")
        return result
