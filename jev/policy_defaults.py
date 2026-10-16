"""三个判断场景的默认 JSONC 文本。

这里只放默认值和注释，不放解析与求值代码，方便单独审阅和修改。
"""

DEFAULT_POLICY_TEXT = {
    "addressed": """{
  "questions": {
    "worthwhile": {
      "type": "noul",
      "label": "值得",
      "instructions": "Would a short in-character response add something welcome here?",
      "criteria": {
        "true": "A useful reply, empathy, reaction, joke, follow-up, or a direct question to this bot. Simple or unclear questions still count.",
        "false": "Repetitive advice, forced help, empty acknowledgment, repeating what others already said. A bare mention with no content, a repeated shout-out, or an insulting mention to this bot is also not worth an answer."
      }
    }
  },
  // 放行规则：外层每组之间是“或”，每组里面的所有条件是“且”。
  // over：分数达到或超过这个数才算过；under：分数必须小于这个数才算过。
  "pass": [
    // 路一：点名消息值得回复。满足下面所有条件才能放行。
    {
      "name": "值得回复",
      "all": [
        // worthwhile 调大更挑剔，调小更容易接。参考：安静 0.56，活泼 0.42。
        { "name": "worthwhile", "over": 0.50 }
      ]
    }
  ]
}
""",
    "intake": """{
  "questions": {
    "intrusive": {
      "type": "noul",
      "label": "打扰",
      "instructions": "Would the bot replying now interrupt or annoy the participants?",
      "criteria": {
        "true": "Private-directed exchange, closing remark, bot already dominates, someone said stop, or stale target.",
        "false": "Room for a relevant short reply, or genuinely continuing the exchange with this bot."
      }
    },
    "continuation": {
      "type": "noul",
      "label": "续话",
      "instructions": "Does this continue an actual recent exchange involving the bot on the same topic?",
      "criteria": {
        "true": "A reply, correction, emotional reaction or shared joke connected to the bot's recent message.",
        "false": "Topic changed, polite closure, another conversation, or only a similar keyword."
      }
    },
    "addressed": {
      "type": "noul",
      "label": "是叫我",
      "instructions": "Is the target speaking to this bot, including a natural follow-up without @?",
      "criteria": {
        "true": "A clear reference or reply to this bot; speaker and reply relation support it.",
        "false": "Talking to others, generic group question, ambiguous pronoun, or merely the same author."
      }
    },
    "worthwhile": {
      "type": "noul",
      "label": "值得",
      "instructions": "Would a short in-character response add something welcome here?",
      "criteria": {
        "true": "A useful reply, empathy, reaction, joke, follow-up, or a direct question to this bot. Simple or unclear questions still count.",
        "false": "Repetitive advice, forced help, empty acknowledgment, repeating what others already said. A bare mention with no content, a repeated shout-out, or an insulting mention to this bot is also not worth an answer."
      }
    }
  },
  // 放行规则：外层每组之间是“或”，每组里面的所有条件是“且”。
  // over：分数达到或超过这个数才算过；under：分数必须小于这个数才算过。
  "pass": [
    // 路一：正接着和机器人有关的话茬。满足下面所有条件才能放行。
    {
      "name": "续话",
      "all": [
        // intrusive 调小更容易被拦，调大更能插话。参考：安静 0.38，活泼 0.55。
        { "name": "intrusive", "under": 0.45 },
        // continuation 调大更难续上，调小更容易接续。参考：安静 0.78，活泼 0.55。
        { "name": "continuation", "over": 0.70 },
        // worthwhile 调大更挑剔，调小更容易接。参考：安静 0.56，活泼 0.42。
        { "name": "worthwhile", "over": 0.50 }
      ]
    },
    // 路二：没写 @，但话明显是说给机器人的。满足下面所有条件才能放行。
    {
      "name": "被点名",
      "all": [
        // intrusive 调小更容易被拦，调大更能插话。参考：安静 0.38，活泼 0.55。
        { "name": "intrusive", "under": 0.45 },
        // addressed 调大不容易自作多情，调小更容易接。参考：安静 0.86，活泼 0.70。
        { "name": "addressed", "over": 0.80 },
        // worthwhile 调大更挑剔，调小更容易接。参考：安静 0.56，活泼 0.42。
        { "name": "worthwhile", "over": 0.50 }
      ]
    },
    // 路三：没人点名，但这句很值得一说，而且几乎不打扰。满足下面所有条件才能放行。
    {
      "name": "主动插话",
      "all": [
        // intrusive 是主动插话的打扰上限，调小更少插嘴，调大更活跃。参考：安静 0.12，活泼 0.35。
        { "name": "intrusive", "under": 0.20 },
        // worthwhile 是主动插话的起评分，调大更挑剔，调小更愿意开口。参考：安静 0.86，活泼 0.70。
        { "name": "worthwhile", "over": 0.78 }
      ]
    }
  ]
}
""",
    "review": """{
  "questions": {
    "still_fits": {
      "type": "noul",
      "label": "还没过时",
      "instructions": "The bot already chose to reply and generated candidate_reply using its full context. New group messages may have arrived while it was thinking. Does this draft STILL fit the target? Only judge whether the new context invalidates it; unrelated interjections alone are not a reason to discard a relevant reply.",
      "criteria": {
        "true": "The intended target and topic remain valid; the reply is not superseded, withdrawn or already answered.",
        "false": "A correction, explicit stop, changed target/topic or intervening answer makes this draft obsolete or repetitive."
      }
    },
    "appropriate": {
      "type": "noul",
      "label": "草稿合适",
      "instructions": "Does candidate_reply fit the persona and conversation without taking over, inventing visual details, or answering the wrong person?",
      "criteria": {
        "true": "Short, relevant, supported by available evidence and fits the role.",
        "false": "Intrusive, stale, repetitive, invented image details, or unfounded personal assumptions."
      }
    },
    "redundant": {
      "type": "noul",
      "label": "草稿重复",
      "instructions": "Is candidate_reply redundant with messages already present?",
      "criteria": {
        "true": "Already answered or near-duplicate bot response.",
        "false": "Adds a useful or natural reaction not already expressed."
      }
    }
  },
  // 放行规则：外层每组之间是“或”，每组里面的所有条件是“且”。
  // over：分数达到或超过这个数才算过；under：分数必须小于这个数才算过。
  "pass": [
    // 路一：草稿没过时、合适、也不重复。满足下面所有条件才能放行。
    {
      "name": "复核通过",
      "all": [
        // still_fits 调大更容易因新消息拦下，调小更愿意照发。参考：安静 0.55，活泼 0.45。
        { "name": "still_fits", "over": 0.50 },
        // appropriate 调大更严格，调小更宽容。参考：安静 0.80，活泼 0.70。
        { "name": "appropriate", "over": 0.75 },
        // redundant 调小更容易嫌重复而拦下，调大更宽容。参考：安静 0.25，活泼 0.45。
        { "name": "redundant", "under": 0.35 }
      ]
    }
  ]
}
""",
}


def default_policy_text() -> dict[str, str]:
    """返回给控制台展示的默认 JSONC 文本副本。"""
    return dict(DEFAULT_POLICY_TEXT)
