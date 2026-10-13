"""不依赖 AstrBot 的判断与撤回状态机。"""

import asyncio
import logging
import sqlite3
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Protocol

from .client import JevError
from .config import Settings
from .history import History
from .policy import (
    Policy,
    decide_intake,
    decide_review,
)

logger = logging.getLogger(__name__)

MAX_SNAPSHOTS = 128
CONVERSATION_BUDGET = 12000


class Judge(Protocol):
    async def evaluate(self, state: dict, stage: str, instructions: str) -> dict: ...


@dataclass
class Message:
    session: str
    message_id: str
    sender: str
    text: str
    private: bool = False
    addressed: bool = False
    recalled: bool = False
    role: str = "user"
    persona: str = ""
    persona_id: str = ""
    persona_status: str = "unresolved"
    speaker_name: str = ""
    conversation: list[str] = field(default_factory=list)


NON_TEXT = "（图片/表情等读不到的内容）"


@dataclass(frozen=True)
class PersonaSnapshot:
    """控制台预览用的会话人格快照，不含从宿主复制的聊天行。"""

    persona: str = ""
    persona_id: str = ""
    persona_status: str = "unresolved"


def speaker(message: Message, names: dict[str, str]) -> str:
    """为本次请求确定说话人标签，优先沿用宿主给出的昵称。

    Args:
        message: 待渲染的消息。
        names: 本次请求内「发送者 ID → 标签」映射，会被就地填充。

    Returns:
        机器人 / 对方 / 宿主昵称 / 成员N 形式的标签。
    """
    if message.role == "assistant":
        return "机器人"
    if message.private:
        return "对方"
    if message.speaker_name:
        return message.speaker_name
    if message.sender not in names:
        names[message.sender] = f"成员{len(names) + 1}"
    return names[message.sender]


def render_line(message: Message, names: dict[str, str]) -> str:
    """把消息渲染成一行可读文本，撤回等内部状态翻译成自然语言。

    Args:
        message: 待渲染的消息。
        names: 本次请求内的说话人映射。

    Returns:
        形如「成员2：活动昨天打完了」的一行文本。
    """
    line = f"{speaker(message, names)}：{message.text or NON_TEXT}"
    return line + "〔这条已被撤回〕" if message.recalled else line


def render_scene(message: Message) -> str:
    """只在确实被点名时写正向说明，避免向模型断言「未被@」。

    Args:
        message: 当前触发消息。

    Returns:
        〔〕内使用的场景说明。
    """
    tags = ["私聊" if message.private else "群聊"]
    if message.addressed:
        tags.append("机器人被@了")
    return "，".join(tags)


class Engine:
    def __init__(self, settings: Settings, judge: Judge, history: History | None):
        self.settings = settings
        self.judge = judge
        self.history = history
        self.personas: OrderedDict[str, PersonaSnapshot] = OrderedDict()
        self.last_replies: OrderedDict[str, str] = OrderedDict()
        self.tombstones: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.busy_locks: dict[str, float] = {}
        self.live: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
        self.burst: OrderedDict[str, int] = OrderedDict()
        self.last_spoke: dict[str, float] = {}
        self.burst: OrderedDict[str, int] = OrderedDict()
        self.last_decision: dict | None = None
        self.history_error = False
        self.decisions = 0
        self.blocked = 0
        self.allowed = 0
        self.policy = Policy()

    def active(self, private: bool) -> bool:
        return self.settings.enabled and (
            self.settings.private_enabled if private else self.settings.group_enabled
        )

    @staticmethod
    def remember(store: OrderedDict, key: str, value) -> None:
        """按最近使用保留有限条快照。

        Args:
            store: 以会话键索引的有序字典。
            key: 会话键。
            value: 快照内容。
        """
        store[key] = value
        store.move_to_end(key)
        while len(store) > MAX_SNAPSHOTS:
            store.popitem(last=False)

    def mark_spoke(self, session: str) -> None:
        """记录机器人刚在这个会话里说了一句，供同会话冷却查表。"""
        self.last_spoke[session] = time.monotonic()
        while len(self.last_spoke) > MAX_SNAPSHOTS:
            self.last_spoke.pop(next(iter(self.last_spoke)))

    def in_cooldown(self, session: str) -> bool:
        """该会话是否还在冷却窗口内；reply_cooldown_seconds 为 0 表示关闭冷却。"""
        seconds = self.settings.reply_cooldown_seconds
        if seconds <= 0 or session not in self.last_spoke:
            return False
        return time.monotonic() - self.last_spoke[session] < seconds

    def burst_arm(self, key: str) -> int:
        """登记一轮防抖窗口，返回值代表本轮序号；先到先得，新来的覆盖旧的。"""
        seq = self.burst.get(key, 0) + 1
        self.burst[key] = seq
        self.burst.move_to_end(key)
        while len(self.burst) > 512:
            self.burst.popitem(last=False)
        return seq

    def burst_current(self, key: str, seq: int) -> bool:
        """本轮还是不是窗口内最新的一条。"""
        return self.burst.get(key) == seq

    def observe(self, message: Message) -> None:
        """登记在途消息、会话人格快照和机器人自己发出的那行。

        Args:
            message: 兼容层在解析完人格后产生的消息；聊天上下文由兼容层从宿主复制，
                引擎不留副本。
        """
        message.text = message.text[: self.settings.text_limit]
        key = (message.session, message.message_id)
        if key in self.tombstones:
            message.recalled = True
        self.live[key] = message
        if message.role == "assistant":
            self.remember(self.last_replies, message.session, render_line(message, {}))
            self.mark_spoke(message.session)
        else:
            self.remember(
                self.personas,
                message.session,
                PersonaSnapshot(
                    message.persona, message.persona_id, message.persona_status
                ),
            )

    def recall(self, session: str, message_id: str) -> None:
        """同步标记撤回，避免被后续限流或异步处理阻塞。

        Args:
            session: 含平台、机器人和群聊/私聊标识的会话键。
            message_id: 原消息 ID，而非通知事件自身 ID。
        """
        key = (session, message_id)
        now = time.monotonic()
        self.tombstones[key] = now
        self.tombstones.move_to_end(key)
        while self.tombstones:
            first, timestamp = next(iter(self.tombstones.items()))
            if len(self.tombstones) <= 10000 and now - timestamp < 3600:
                break
            self.tombstones.pop(first)
        if message := self.live.get(key):
            message.recalled = True

    def busy(self, key: str) -> bool:
        """同会话上一条回复还在途时视为忙，超时自动解锁。"""
        now = time.monotonic()
        timeout = self.settings.busy_lock_timeout_seconds
        for lock_key, locked_at in list(self.busy_locks.items()):
            if now - locked_at > timeout:
                self.busy_locks.pop(lock_key, None)
        return key in self.busy_locks

    def acquire(self, key: str) -> None:
        """接管一条消息时上锁，实际发送完成或确定不发时解锁。"""
        self.busy_locks[key] = time.monotonic()

    def release(self, key: str | None) -> None:
        if key:
            self.busy_locks.pop(key, None)

    async def decide(self, stage: str, message: Message, candidate: str = "") -> bool:
        """按阶段题库做结构化判断，逐项概率由确定性规则合成放行结论。

        Args:
            stage: pre、post、final 或 probe。
            message: 原始触发消息，其 conversation 由兼容层从宿主复制。
            candidate: 完整回复的有界文本。

        Returns:
            是否放行；功能关闭时完全旁路。
        """
        cfg = self.settings
        if stage != "probe" and not self.active(message.private):
            return True
        policy = self.policy
        lines = list(message.conversation)
        previous = (
            self.last_replies.get(message.session) if not message.private else None
        )
        if previous:
            lines.insert(0, previous)
        picked = []
        remaining = CONVERSATION_BUDGET
        for line in reversed(lines):
            if remaining <= 0:
                break
            cut = line[:remaining]
            remaining -= len(cut)
            picked.append(cut)
        conversation = list(reversed(picked))
        names: dict[str, str] = {}
        state = {
            "bot_persona": message.persona if policy.uses_persona else "",
            "target_message": f"{render_line(message, names)}〔{render_scene(message)}〕",
            "recent_conversation": conversation,
            "candidate_reply": candidate[: cfg.text_limit],
        }
        record = {
            "timestamp": time.time(),
            "stage": stage,
            "session": message.session,
            "message_id": message.message_id,
            "state": state,
            "values": None,
            "allowed": True,
            "reason": "disabled",
            "questions": policy.questions(stage, message.persona),
        }
        start = time.monotonic()
        if cfg.recall_enabled and message.recalled:
            record.update(allowed=False, reason="recalled")
        elif stage == "final":
            # 时效判断已合并进发送复核三题，这里只保留撤回硬规则。
            return True
        elif (
            cfg.bypass_addressed
            and message.addressed
            and stage in ("pre", "post")
            and (
                not cfg.bypass_addressed_whitelist
                or str(message.sender).strip() in cfg.bypass_addressed_whitelist
            )
        ):
            record.update(allowed=True, reason="addressed_bypass")
        elif stage == "probe" or (
            cfg.pre_check_enabled if stage == "pre" else cfg.post_check_enabled
        ):
            try:
                result = await self.judge.evaluate(state, stage, record["questions"])
                record.update(result)
                values = result["values"]
                if stage in ("pre", "probe"):
                    verdict = decide_intake(
                        values,
                        cfg.reply_style,
                        intrusive_max=cfg.intrusive_block_threshold,
                        join_intrusion_ceiling=cfg.join_intrusion_ceiling,
                        continuation_min=cfg.continuation_threshold,
                        addressed_min=cfg.addressed_threshold,
                        worthwhile_min=cfg.worthwhile_min,
                    )
                else:
                    verdict = decide_review(
                        values,
                        freshness_min=cfg.review_freshness_min,
                        appropriate_min=cfg.review_appropriate_min,
                        redundant_max=cfg.review_redundant_max,
                    )
                record.update(allowed=verdict.allowed, reason=verdict.reason)
                if cfg.dry_run_enabled and not verdict.allowed:
                    # 影子模式：记录“本会拦截”，实际放行，只有撤回硬规则不受影响。
                    record.update(dry_run=True, computed_allowed=False, allowed=True)
            except JevError as error:
                record.update(
                    allowed=cfg.fail_open, reason="judge_error", error=str(error)
                )
            if cfg.recall_enabled and message.recalled:
                record.update(allowed=False, reason="recalled_during_check")
        record["elapsed_ms"] = round((time.monotonic() - start) * 1000, 1)
        await self.record(record)
        # 审计落盘也会让出执行权，不能在这段时间漏掉撤回。
        if cfg.recall_enabled and message.recalled and record["allowed"]:
            return await self.decide("final", message, candidate)
        return record["allowed"]

    async def record(self, record: dict) -> None:
        """更新运行状态，历史写入失败不泄露上下文到日志。

        Args:
            record: 本次判断记录。
        """
        self.decisions += 1
        self.blocked += int(not record["allowed"])
        self.allowed += int(bool(record["allowed"]))
        self.last_decision = {
            key: record[key]
            for key in ("timestamp", "stage", "allowed", "reason", "elapsed_ms")
        }
        if self.settings.history_enabled and self.history:
            try:
                await asyncio.to_thread(self.history.append, record)
                self.history_error = False
            except (OSError, ValueError, sqlite3.Error):
                self.history_error = True
                logger.error("Jev history write failed")
