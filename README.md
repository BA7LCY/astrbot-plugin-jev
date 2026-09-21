# Jev 接话判断

一个小型 AstrBot 插件：Jev 负责「要不要接话 / 要不要发送」，回复内容仍由 AstrBot 当前 LLM 生成人格化回答。

## 安装与启用

1. 将此目录放入 AstrBot 的 `data/plugins`，在插件管理中加载。当前针对 AstrBot **4.27.4** 验证，元数据限定 `<4.28.0`。
2. 在插件配置填写 TypeSafe `api_key`。使用官方 `POST /v1/systemone`，不是 OpenAI 兼容接口；模型默认 `jev-latest`。
3. 开启 `enabled`。群聊默认纳入判断；私聊必须另开 `private_enabled`。插件默认总开关关闭，防止未配置时意外拦截。
4. 控制台无需额外开关或端口：重新加载插件后，在 AstrBot WebUI 的插件管理中进入本插件详情页，打开 **Jev 控制台** Page。新增或删除 `pages/` 下的目录需要重载插件才会被发现。

已有 `aiohttp` 即可，无额外 SDK、前端构建或数据库服务。修改宿主插件配置后重新加载；Page 内模板保存后下一次判断立即生效。此仓库不修改宿主 dashboard/OpenAPI，因此不需要生成宿主 API client。

## 提示词模板与人格变量

控制台 Page「判断规则」提供两套可编辑模板：**接话判断**、**发送复核**。

- `{{persona}}`：当前会话实际生效的 AstrBot 人格提示词，也是模板中「机器人设定」这一行的唯一来源。**模板里写了这个变量就注入，没写就完全不向宿主解析、不发送**，没有额外开关；默认模板两套都带 `{{persona}}`。
- 人格取值复用宿主的选择顺序：会话强制覆盖 → 当前对话人格（显式 `[%None]` 即空，不擅自套用其他人格）→ 配置默认人格；只取人格的 `prompt` 字段，不含示例对话、工具、技能，也不含宿主自己拼装的系统提示，最长 6000 字符。
- 变量只做一次文字替换，不执行 Python/Jinja 等模板代码；同一模板里 `{{persona}}` 可以出现多次，取值相同。
- 预览不调用模型，可选择近期会话的人格快照。note 会区分三种情况：草稿未写 `{{persona}}`、草稿引用了但快照还没有人格文本（等下一条真实消息后再看）、以及正常取到人格（附字符数）。
- 默认模板按钮只填入编辑器，**保存后**才生效。模板保存在插件数据目录 `policy.json`；每次保存保留上一份 `.json.bak`。

判断历史保存当次实际展开模板、人格 ID/状态、上下文、候选回复、模型版本、概率、阈值、用量、耗时及最终结果。Jev 的 Noul 返回概率而不是解释文本，本插件只展示真实规则原因，不编造模型思维链。

## 开关与行为

| 配置 | 行为 |
| --- | --- |
| `enabled` | 总开关；关闭后不改变消息的宿主处理方式 |
| `group_enabled` / `private_enabled` | 分别控制群聊 / 私聊是否接管 |
| `pre_check_enabled` | 判断是否接话；放行时唤醒宿主 LLM，拒绝时停止该消息的后续处理 |
| `post_check_enabled` | LLM 完整生成后，使用最新上下文复核候选回复 |
| `recall_enabled` | 独立硬规则：原消息撤回后不发送，不受 API 故障放行影响 |
| `context_enabled` | 内存收集近期受管消息和实际发送的机器人回复 |
| `history_enabled` | 保存并开放历史查询；关闭不会删除已保存记录 |
| `bypass_commands` | 默认对宿主已识别的插件命令旁路 |
| `fail_open` | 默认关闭；Jev API 失败拒绝，开启则放行，但撤回仍拒绝。人格解析失败只会让人格为空，不影响放行判定 |

控制台没有独立开关、端口或令牌：插件加载后即作为宿主 Pages 提供。

上下文默认每会话 20 条、最多 128 个会话，单条 2000 字符且总文本预算 12000 字符。历史默认保留最近 1000 条，SQLite 跨重启保留，内存上下文不跨重启。

发送复核或撤回保护开启时，受管消息在 LLM 开始前关闭流式输出。Jev 复核发生在最终回复装饰钩子，并在真正调用该事件的 `send()` 前再次检查撤回，以覆盖图片/TTS 转换和分段发送延迟。

## 边界与隐私

- 撤回事件目前支持 **OneBot v11** 的 `group_recall` / `friend_recall`。其他平台未接入撤回转换，不宣称具有撤回保护。
- 保留 AstrBot 白名单、会话开关、权限与限流管道。上下文仅覆盖通过这些管道并被插件观察到的消息。
- 本插件针对宿主回复管道。工具、其他插件直接调用独立发送 API、非标准 Agent 输出或更晚修改发送方法的插件可能绕过检查。与其他主动接话插件同时启用可能冲突。
- 原消息已经进入平台网络发送后的撤回无法追回；分段已经发送出去的部分无法撤销。
- 接话拒绝使用 `stop_event()`，会阻止本条消息后续插件处理；注册命令默认旁路。发送拒绝不会回滚宿主可能已写入的 LLM 对话历史。
- Jev 仅接收文本/结构化文本。图片、语音本身不上传到 Jev，依赖宿主预处理得到的文本；超长内容会截断，可能影响判断。
- 开启判断意味着相关聊天文本会发送至 **TypeSafe**；启用人格变量也会向其发送人格提示词。只在获授权的群聊使用。
- 本地历史含聊天内容及人格文本，**明文 SQLite** 存储于 AstrBot 数据目录的 `plugin_data/jev/history.db`。请使用系统目录权限保护；关闭历史不会自动擦除旧数据。
- 控制台是宿主 Dashboard 内嵌 Page，不再有独立端口、监听地址或令牌。页面运行在受限 iframe（`allow-scripts allow-forms allow-downloads`）中，只能经 `window.AstrBotPluginPage` bridge 访问本插件注册的 API，读不到 Dashboard 的 cookie 与 localStorage。鉴权复用 Dashboard 登录态，因此**任何能登录 Dashboard 的用户都可编辑判断模板**；请把 Dashboard 本身放在受控网络内。
- API 错误仅保存安全错误码，不记录密钥或 HTTP 错误正文。401/429/529/超时不自动重试，避免积压消息或额外请求。
- 面板的「放行」是判断结果，不是送达确认。「调用 Jev 测试连接」只发送虚构问候，会产生真实用量，不测试真实聊天效果。

## 验证

```bash
python -m pytest -q --basetemp=.pytest-tmp-tests
python -m ruff format --check main.py jev tests
python -m ruff check main.py jev tests
node --check pages/console/app.js
git diff --check
```

测试通过依赖注入使用假 Jev，不读取真实密钥或向真实聊天发送消息。宿主导入测试把 `ASTRBOT_ROOT` 指向插件内的隔离测试目录。现有 `scripts/smoke_live.py` 是遗留导演插件脚本，与本插件无关，不应运行。

官方协议参考：`https://docs.typesafe.ai/api`、`https://docs.typesafe.ai/primitives/noul`。部署仅在明确要求后使用已有 `scripts/deploy_git.sh`，不自动执行。
