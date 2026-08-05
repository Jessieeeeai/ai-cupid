# AI丘比特(代号 hongniang)

> 一个活在你 AI 对话框里的丘比特。不用下载 App —— 把这个仓库发给你的 AI,它就能帮你找对象。

---

## 🤖 如果你是 AI:照这里做

用户把本仓库发给你,意味着 TA 想用 AI丘比特找对象。**服务地址:`https://web-production-63f51.up.railway.app`**(下称 `$BASE`)。

**如果你能执行 HTTP 请求**(有终端/网络工具,如 Claude Code、Cowork、Cursor 等),直接调用 REST API,无需任何配置:

```
所有接口均为 POST,JSON body,Content-Type: application/json

1. 注册            POST $BASE/api/register/start        {}
                   → 返回 visit_code(回访码)和第一题
2. 逐题回答        POST $BASE/api/answer                {"visit_code","answer"}
                   → 返回下一题(含 options 可选项);error 非空则让用户重答
3. 照片上传链接    POST $BASE/api/upload_link           {"visit_code"}
                   → 把返回的 upload_url 给用户点开传照片(15分钟有效)
   ★ 如果用户直接把照片发到了对话里且你能访问该文件:不用给链接,
     取 upload_url 末尾的 token,替用户直接传(最多3张):
     curl -F "files=@/路径/photo.jpg" $BASE/api/upload/<token>
4. 今日推荐        POST $BASE/api/recommendations       {"visit_code"}
5. 付费打招呼      POST $BASE/api/greeting              {"visit_code","target_id","message","chain":"solana|base"}
                   → 返回付款金额(唯一尾数,必须一分不差)和收款地址
6. 查付款状态      POST $BASE/api/order/status          {"visit_code","order_id"}
7. 我的信箱        POST $BASE/api/inbox                 {"visit_code"}
8. 同意/拒绝       POST $BASE/api/greeting/respond      {"visit_code","greeting_id","accept":true|false}
9. 查看联系方式    POST $BASE/api/contact               {"visit_code","greeting_id"}
10. 余额           POST $BASE/api/balance               {"visit_code"}
11. 改答案         POST $BASE/api/update_answer         {"visit_code","key","answer"}
```

**行为准则**:每次把 2-3 道相关的题打包一起问,用户一条消息答完后按顺序逐个提交,哪题校验失败单独重问;对用户的每个回答先给一句走心的回应再问下一题,你是红娘不是表单;注册后立刻把回访码给用户并强调保存(唯一凭证);展示推荐时,**请你亲自对比双方资料重写推荐理由**(返回的 reason 只是草稿),引用具体细节,像懂行的朋友介绍;涉及付款时把"金额必须精确到最后一位小数"讲清楚;转述拒绝时语气委婉;资料性数据如实展示,不要编造。

**如果你不能执行 HTTP 请求**(纯聊天应用):引导用户二选一——
a) 在支持 MCP 的平台添加连接器,地址 `$BASE/mcp`(Claude:设置→Connectors→Add custom connector);
b) 直接打开网页版 `$BASE`,全程点选即可完成。

---

## 用户怎么用(30 秒)

**方式一:复制这段提示词,粘贴给你的 AI**(Claude / ChatGPT 等):

```
我想用「AI丘比特」找对象。它是一个 MCP 服务,地址:
https://<你的域名>/mcp

请这样帮我:
1. 如果你所在的平台支持添加 MCP 连接器(比如 Claude 的 设置→Connectors→Add custom
   connector),请一步一步教我把上面这个地址加进去。
2. 添加成功后,调用 register_start 帮我注册,然后帮我填 16 题问卷:每次把 2-3 道相关的题一起问我,我一条消息答完你就逐个提交。
3. 提醒我保存好回访码(唯一凭证),并引导我上传 3 张照片。
4. 以后我每天问你"今天有推荐吗",你就调用 get_recommendations 给我看今日推荐。
5. 如果你没法添加 MCP 连接器,就告诉我打开 https://<你的域名> 在网页完成,一样能用。
```

(网页首页右上角"在你的 AI 里用"按钮可一键复制这段。)

**方式二:直接用网页版** `https://<你的域名>/` —— 全程点选,不用装任何东西。

之后:保存好**回访码**(唯一凭证);看到心动的人 1U 打个招呼,对方同意后互换联系方式 💘

## 规则(透明公示)

- 看推荐、被人喜欢、同意/拒绝:**永远免费**
- 付费打招呼 1U;对方拒绝或 72 小时未回应 → **全额退回你的余额**
- 余额可用于下次打招呼/加看推荐,不可提现
- 你的联系方式只在**双方都同意**后互相可见;通知渠道(TG/邮箱)永不展示给任何人
- 不实名,服务器只存最少数据,随时可说"删除我的账号"

---

## 自部署指南(项目所有者看这里)

### 1. 配置

```bash
cp .env.example .env   # 填入你的收款地址等
```

必填:`HN_SECRET_KEY`(随机长字符串)、`HN_SOLANA_ADDRESS` / `HN_BASE_ADDRESS`(收款地址)、`HN_BASE_URL`(对外域名)。
选填:`HN_TELEGRAM_BOT_TOKEN`(TG 通知)、`HN_RESEND_API_KEY`(邮件通知)、`HN_ANTHROPIC_API_KEY`(高质量推荐理由)、`HN_OPENAI_API_KEY`(高质量匹配 embedding)。
不填 LLM key 也能跑,匹配和推荐理由用内置降级实现。

### 2. 本地跑

```bash
pip install -r requirements.txt
python main.py          # http://localhost:8000
pytest                  # 19 个测试
```

### 3. 生产部署(Docker)

```bash
docker build -t hongniang .
docker run -d --env-file .env -p 8000:8000 -v ./data:/app/data hongniang
```

前面套一层 Caddy/Nginx 上 HTTPS(MCP 客户端要求 https)。数据库默认 SQLite(`data/` 目录);
用户量上来后把 `HN_DATABASE_URL` 换成 Postgres 即可,代码不用改。

### 4. 扫链 webhook(到账秒确认)

- **Solana**:Helius → Webhooks → 监听你的收款地址,URL 填 `https://<域名>/webhooks/chain/solana`,Header `x-webhook-secret` 填 `HN_CHAIN_WEBHOOK_SECRET`
- **Base**:Alchemy → Notify → Address Activity,URL 填 `https://<域名>/webhooks/chain/base`,同上

### 5. Telegram bot(通知 + 第三入口)

@BotFather 建 bot 拿 token 填入 `.env`,然后设置 webhook:

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<域名>/webhooks/telegram"
```

### 6. 定时维护(订单/打招呼过期处理)

```bash
# crontab:每小时
0 * * * * curl -s -X POST https://<域名>/internal/tick -H "x-webhook-secret: <你的secret>"
```

## 架构一图流

```
Claude/ChatGPT ──MCP──┐
任意AI+浏览器 ──网页──┤──> FastAPI 单体 ──> SQLite/Postgres
Telegram ──bot────────┘         ├─> 照片存储(本地/R2)
Helius/Alchemy ──webhook──>     ├─> 扫链对账(唯一尾数金额)
                                └─> LLM(推荐理由/embedding,可降级)
```

## 项目结构

```
app/
  config.py         全部配置(品牌/价格/风控参数)
  models.py         数据模型(用户/问卷/推荐/打招呼/订单/流水/事件)
  questionnaire.py  16题问卷状态机
  matching.py       匹配引擎(硬过滤+软打分)
  llm.py            LLM/embedding 适配层(带离线降级)
  payments.py       订单/唯一尾数/余额ledger
  greetings.py      双向打招呼状态机
  chains.py         Helius/Alchemy webhook 解析
  notify.py         TG/邮件通知
  mcp_server.py     MCP 工具集(13个工具)
  api.py            REST API + webhook + 网页路由
  web/              网页入口(对话式)+ 上传页
tests/              19 个测试(问卷/匹配/支付/状态机)
```

## License

MIT
