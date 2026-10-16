"""System One HTTP 协议；基址可指向官方 TypeSafe 或转发网关。"""

import asyncio
import json
import math

import aiohttp

from .config import Settings


class JevError(Exception):
    """仅携带安全错误码，不包含响应正文或凭据。"""


class JevClient:
    def __init__(self, settings: Settings, session: aiohttp.ClientSession):
        self.settings = settings
        self.session = session
        self.slots = asyncio.Semaphore(4)

    async def evaluate(self, state: dict, stage: str, questions: dict) -> dict:
        """提交一组结构化判断题；总超时包含并发排队时间。

        Questions are sent verbatim; this layer does not invent criteria.

        Args:
            state: 待判断的上下文与候选回复。
            stage: 接话、发送或探针阶段（仅用于记录，不进请求）。
            questions: policy 层给定的完整 questions 字典。

        Returns:
            逐项概率、实际模型和用量，不伪造自然语言推理。

        Raises:
            JevError: 配置、网络、HTTP 或响应格式错误。
        """
        if not self.settings.api_key.strip():
            raise JevError("missing_api_key")
        payload = {
            "model": self.settings.model,
            "state": state,
            "questions": questions,
        }
        try:
            return await asyncio.wait_for(
                self._request(payload, questions), timeout=self.settings.timeout_seconds
            )
        except asyncio.TimeoutError:
            raise JevError("timeout") from None
        except aiohttp.ClientError:
            raise JevError("network_error") from None

    async def _request(self, payload: dict, questions: dict) -> dict:
        """执行受限请求并严格验证返回值。

        Args:
            payload: 官方协议的请求体。
            questions: 本次提交的题目，用于逐项核对返回。

        Returns:
            经过验证的判断数据。

        Raises:
            JevError: 服务拒绝请求或返回非法数据。
        """
        async with self.slots:
            async with self.session.post(
                f"{self.settings.api_base}/v1/systemone",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key.strip()}"},
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise JevError(f"http_{response.status}")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise JevError("response_too_large")
                try:
                    body = json.loads(raw)
                    answers = body["answers"]
                    if not isinstance(body["model"], str):
                        raise ValueError
                    values = {}
                    for name, question in questions.items():
                        answer = answers[name]
                        if answer["type"] != question["type"]:
                            raise ValueError
                        if question["type"] == "noul":
                            value = answer["noul"]
                            if (
                                type(value) not in (int, float)
                                or not math.isfinite(value)
                                or not 0 <= value <= 1
                            ):
                                raise ValueError
                            values[name] = float(value)
                        elif question["type"] == "choice":
                            value = answer["choice"]
                            if not isinstance(value, str) or value not in question["criteria"]:
                                raise ValueError
                            values[name] = value
                        else:
                            value = answer["score"]
                            upper = len(question["criteria"]) - 1
                            if (
                                type(value) not in (int, float)
                                or not math.isfinite(value)
                                or not 0 <= value <= upper
                            ):
                                raise ValueError
                            values[name] = float(value)
                    if not values:
                        raise ValueError
                    usage = body.get("usage", {})
                    if not isinstance(usage, dict):
                        raise ValueError
                    safe_usage = {
                        key: usage[key]
                        for key in ("input_tokens", "output_tokens")
                        if type(usage.get(key)) is int and usage[key] >= 0
                    }
                    return {
                        "values": values,
                        "model": body["model"][:100],
                        "usage": safe_usage,
                    }
                except (ValueError, KeyError, TypeError, UnicodeError):
                    raise JevError("invalid_response") from None
