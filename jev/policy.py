"""可编辑的判断模板，变量替换不执行代码。"""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

PRE_PROMPT = """你在决定聊天机器人是否应该接话，而不是生成回复。
机器人设定：{{bot_description}}
可选的 AstrBot 当前会话人格：
{{persona}}

根据 state.target 和 state.conversation 判断现在接话是否自然、必要：
- 用户明确向机器人提问、求助或接续机器人的话题时，倾向接话。
- 群友在互相聊天、重复刷屏、无关闲聊时，倾向沉默。
- 不要为了体现存在感而插话；尊重用户让机器人停止回复的要求。
- 私聊中可更积极，但仍需判断消息是否需要回应。
是否应该接话？"""

POST_PROMPT = """判断 state.candidate_reply 是否仍适合现在发送，而不是生成新回复。
机器人设定：{{bot_description}}
可选的 AstrBot 当前会话人格：
{{persona}}

结合原消息 state.target 和最新 state.conversation：
- 用户已取消问题、话题已过时、回复重复或明显不相关时，不要发送。
- 回复符合当前对话需要，并且没有打断他人时，可以发送。
是否应该发送这条候选回复？"""


@dataclass(frozen=True)
class Policy:
    pre_prompt: str = PRE_PROMPT
    post_prompt: str = POST_PROMPT
    include_persona: bool = False

    @classmethod
    def parse(cls, values: dict) -> "Policy":
        """严格检查模板内容和允许的变量。

        Args:
            values: 控制台 Page 提交或磁盘读入的数据。

        Returns:
            已验证的模板。

        Raises:
            ValueError: 字段、类型、长度或变量非法。
        """
        if not isinstance(values, dict) or set(values) != {
            "pre_prompt",
            "post_prompt",
            "include_persona",
        }:
            raise ValueError("模板字段必须为 pre_prompt、post_prompt、include_persona")
        if type(values["include_persona"]) is not bool:
            raise ValueError("人格开关必须为布尔值")
        for name in ("pre_prompt", "post_prompt"):
            text = values[name]
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 6000:
                raise ValueError("每个模板需包含 1～6000 个字符")
            unknown = set(re.findall(r"\{\{(.*?)\}\}", text)) - {
                "persona",
                "bot_description",
            }
            if unknown:
                raise ValueError("仅支持 {{persona}} 和 {{bot_description}} 变量")
            if any(
                text.count("{{" + variable + "}}") > 1
                for variable in ("persona", "bot_description")
            ):
                raise ValueError("每个变量在一个模板中最多插入一次")
        return cls(**values)

    def render(self, stage: str, description: str, persona: str) -> str:
        """一次性替换变量，变量值内的模板语法不会再次展开。

        Args:
            stage: pre/probe 使用接话模板，其他使用发送模板。
            description: 插件中的机器人设定。
            persona: 当前会话已解析的人格。

        Returns:
            该次判断的最终指令。
        """
        values = {
            "bot_description": description,
            "persona": persona if self.include_persona else "",
        }
        template = self.pre_prompt if stage in ("pre", "probe") else self.post_prompt
        return re.sub(
            r"\{\{(persona|bot_description)\}\}",
            lambda match: values[match.group(1)],
            template,
        )

    def save(self, path: Path) -> None:
        """原子保存模板，并保留上一份配置备份。

        Args:
            path: 插件数据目录中的模板文件。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.with_suffix(".json.bak").write_bytes(path.read_bytes())
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
