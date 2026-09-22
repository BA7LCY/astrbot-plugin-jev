# Jev 接话判断

AstrBot 插件。机器人在开口之前，先让 Jev 判断「这句该不该接」；回复写完之后，再判断「这句该不该发」。
回复内容仍然由 AstrBot 原本的语言模型和人格生成，本插件只决定发或不发，不改写一个字。

仓库地址：https://github.com/LovieCode/astrbot-plugin-jev

## 你需要准备

- AstrBot **4.27.4**（本插件声明兼容 `<4.28.0`）。
- 一个 Jev 判断接口的密钥：官方 TypeSafe、OpenRouter、Vercel AI Gateway，三家任选一家。
- Python 依赖只用 AstrBot 已经装好的 `aiohttp`。不需要编译前端，也不需要另外部署数据库。

## 安装

1. 把本仓库放到 `AstrBot/data/plugins/astrbot-plugin-jev`（`git clone` 或者下载 zip 解包都行），然后在 AstrBot 的插件管理里重载插件列表。
2. 打开本插件的配置项，填入 `api_key`；如果想换一家接口入口，就同时改 `api_base` 和 `model`（见下表）。
3. 打开总开关 `enabled`。**这一步还不会改变聊天行为**：群聊接管 `group_enabled` 和私聊接管 `private_enabled` 默认都是关的，需要哪种场景就单独打开哪个。
4. 想改判断规则、看每次判断的记录：进入本插件详情页，打开 **Jev 控制台** 页面。控制台不额外开端口，也不需要单独的访问令牌。
5. 改完插件配置需要重载插件才生效；在控制台里保存模板后，下一次判断立刻用新规则。

## 接口入口

三家转发的是同一套接口，插件会自己把请求路径补全，所以 `api_base` **只填地址本身**，末尾斜杠写不写都行。换入口时 `api_key` 和 `model` 都要跟着换。

| 入口 | `api_base` | `model` |
| --- | --- | --- |
| 官方 TypeSafe（默认） | `https://api.typesafe.ai` | `jev-latest` |
| OpenRouter | `https://openrouter.ai/api` | `jev-latest` 或 `typesafe/jev-1.13` |
| Vercel AI Gateway | `https://ai-gateway.vercel.sh/typesafe` | `typesafe-ai/jev` |

默认的放行门槛是在官方接口上实测出来的。换了别家之后，先看判断记录里的概率分布，再考虑动门槛。

## 配置项

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| `enabled` 总开关 | 关 | 关掉时本插件完全不管聊天 |
| `group_enabled` 接管群聊 | 关 | 群里没被 @ 的普通发言也先判断该不该接 |
| `private_enabled` 接管私聊 | 关 | 私聊普通消息也先判断该不该回 |
| `pre_check_enabled` 接话前先判断 | 开 | 第一道判断，不通过就不回 |
| `post_check_enabled` 发送前再复核 | 开 | 第二道判断，回复写完后再决定发不发；开启后这段回复不能逐字往外冒，出现会稍慢 |
| `recall_enabled` 原消息撤回后不再回复 | 开 | 对方撤回就不发出去了；属于硬性规则，只支持以 OneBot 协议接入的 QQ |
| `history_enabled` 保存判断记录 | 开 | 记录存在本机，含聊天原文；关掉只停新增，不删旧的 |
| `bypass_commands` 指令跳过判断 | 开 | `/help` 这类指令照常执行，不会被拦 |
| `fail_open` 判断失败时是否放行 | 关 | 关：接口超时或报错就不回；开：宁可多发，打扰会变多 |
| `api_key` 接口密钥 | 空 | 只填在这一行，不要写进模板 |
| `api_base` 接口地址 | 官方地址 | 只填地址本身，请求路径由插件补 |
| `model` 模型名 | `jev-latest` | 三家叫法不同，见上表 |
| `timeout_seconds` 单次判断最多等几秒 | `8.0` | 可填 0.1～60，含排队时间，超时不重试 |
| `threshold` 放行门槛（概率） | `0.5` | 可填 0～1，概率大于等于它才放行 |
| `text_limit` 单条文本长度上限 | `2000` | 可填 100～4000，超了会截断 |
| `history_limit` 记录保留条数 | `1000` | 可填 1～10000，超出自动删最早的 |

## 它是怎么判断的

- **两道关卡**：收到消息先判断要不要开口；放行以后交给 AstrBot 原有的语言模型生成回复；生成完成，再拿最新的聊天记录把准备发的内容复核一次。
- **判断时看到的聊天内容，直接跟着 AstrBot 走**：群聊读 AstrBot 内置主动回复功能那份群聊记录，私聊读当前会话已经存下的对话历史。本插件不再自己攒一份聊天记录，所以 AstrBot 重启、或者你把 `group_message_max_cnt` 改大改小，判断能看到的范围会跟着变。机器人自己上一句由本插件补上（复核时需要）。
- **每次上传的就是三段可读文本**：当前这句（谁说的、什么场景）、前面的上下文（按时间正序）、准备发送的回复原文。平台名、会话 ID、消息 ID、人格编号这些内部信息都不发。
- **规则由你写**：控制台里两套模板（接话判断 / 发送复核）可以改。默认模板不替你规定什么该沉默，只说明三段内容是什么、要回答哪个问题；接话尺度可以取当前会话实际生效的 AstrBot 人格提示词，写法是模板里的 `{{persona}}`。模板里写了这个变量才会去读取并发送，没写就完全不发。
- **记录可查**：每条判断保存当次展开后的模板、上下文、候选回复、概率、门槛、用量和耗时。Jev 给的是概率而不是理由，所以面板只展示真实的规则原因，不编造模型想法。

## 边界与隐私

- 接管哪些会话由 AstrBot 的两层白名单共同决定，任何一层填了内容且不命中，本插件就完全不介入（不发请求、不读聊天内容、不改回复方式）：`platform_settings.id_whitelist` 和 `provider_ltm_settings.active_reply.whitelist`（后者只约束群聊）。
- 被拒的接话消息只掐断 AstrBot 默认的语言模型链路，**仍会交给其他插件处理**。
- 复核走的是 AstrBot 正常的回复流程。如果回复是工具调用的结果、别的插件自己直接调发送接口、或者有比本插件更晚才决定发送方式的插件，复核可能被绕过。同时启用别的主动接话插件也可能冲突。
- 消息已经发到平台上之后的撤回无法追回；分段发送里已经发出去的那几段也撤不回。
- 撤回拦截目前只支持 **OneBot v11** 协议的 QQ（群撤回和好友撤回），其他平台没有这项保护，也不作承诺。
- 图片和语音本身不上传，只传 AstrBot 预处理出来的文字；超长内容会截断。
- 开启判断，就等于把这些聊天文字送到 `api_base` 指向的服务（密钥也是原样发给它）；用了人格变量还会连带发送人格提示词。请只在获授权的群里使用。
- 判断记录是**未加密的本地数据库文件**，存在 `data/plugin_data/jev/history.db`，里面有聊天内容和人格文本。请靠系统目录权限保护，关闭记录功能不会删掉旧数据。
- 控制台内嵌在 AstrBot 后台页面里，读不到后台的登录凭据和本地存储；但它复用后台的登录状态，所以**任何能登录 AstrBot 后台的人都能改判断模板**。后台本身请放在受控的网络里。
- 接口报错只保存可公开的错误码，不记录密钥和响应正文。密钥不对、被限流、服务繁忙、超时这几种情况都不自动重试，避免消息积压。
- 面板上的「放行」只是判断结果，不代表消息已经送达。「测试连接」会发送一段虚构的问候语，产生真实用量，而且只能验证网络通不通。

## 常见问题

- **装好开了总开关却没反应**：群聊和私聊接管默认都是关的，需要各自单独打开；另外会话还得在 AstrBot 的两层白名单内。
- **判断记录里上下文只有几条**：上下文跟着 AstrBot 的状态走，AstrBot 重启或插件重载后内存记录会被清空，这是正常现象。
- **改了门槛没看到效果**：同一份请求重复调用只差约 0.02，但隔半小时重测会漂移 0.1～0.2，小改动看不出来。
- **想确认现在打的是哪家接口**：控制台「运行状态」那一行会显示当前的 `api_base`。

## 开发自测

```bash
python -m pytest -q --basetemp=.pytest-tmp-tests
python -m ruff format --check main.py jev tests
python -m ruff check main.py jev tests
node --check pages/console/app.js
```

测试全部用假 Jev，不读真实密钥、不向真实聊天发送消息；涉及 AstrBot 的测试会把 `ASTRBOT_ROOT` 指到插件内的隔离目录，不会碰你的真实数据。

协议参考：TypeSafe <https://docs.typesafe.ai/api>、<https://docs.typesafe.ai/primitives/noul>；OpenRouter <https://openrouter.ai/docs/guides/community/typesafe-sdk>；Vercel AI Gateway <https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe>。
