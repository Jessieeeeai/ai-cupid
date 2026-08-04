# AI丘比特(代号 hongniang)

> 一个活在你 AI 对话框里的丘比特。不用下载 App —— 在 Claude / ChatGPT 里加一个链接,AI 就能帮你找对象。

## 用户怎么用(30 秒)

1. 在 Claude(设置 → Connectors → Add custom connector)或 ChatGPT 里添加 MCP 地址:`https://<你的域名>/mcp`
2. 对 AI 说:"帮我注册AI丘比特" —— AI 会陪你聊完 20 个小问题,给你链接传 3 张照片
3. 保存好你的**回访码**(唯一凭证)
4. 每天问一句"今天有推荐吗",看到心动的人,1U 打个招呼;对方同意后,互换联系方式 💘

没有支持 MCP 的 AI?直接打开网页版:`https://<你的域名>/`,体验一样。

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
  questionnaire.py  20题问卷状态机
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
