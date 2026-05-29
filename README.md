# Telegram 频道抓取 · AI 改写 · 批量发布

抓取源频道帖子（相册+配文），在网页瀑布流预览后批量发布，支持广告过滤、去源站信息、每日定时抓取、TG Bot 推送。

## 两种运行方式

### 方式 A：网页控制台（推荐）

```bash
cd /Users/kinijoey/Desktop/telegrambot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 可先填 API_ID、API_HASH
python web_app.py
```

浏览器打开：**http://127.0.0.1:8765**

1. 填写 API_ID / API_HASH，保存配置  
2. 用手机号登录 Telegram（验证码、二步验证）  
3. 填写源频道、目标频道，按需开启 AI 复写  
4. 可选点「立即抓取一次」补抓最近帖子  
5. 在瀑布流里选择帖子批量发布

### 方式 B：纯命令行

```bash
source .venv/bin/activate
# 编辑 .env 或 settings.json
python forwarder.py
```

首次若未登录，需先在网页完成登录，或自行用 Telethon 生成 `user.session`。

## 流程说明（适配相册+评分配文类频道）

```
源频道新帖（相册 grouped_id + 配文）
    ↓
整组抓取：所有图片 + 配文（配文可能在组内任意一条）
    ↓
网页瀑布流待发布池（支持批量选择）
    ↓
去广告过滤（机场/VPN/推广）+ 去源站痕迹 + 自动排版 + AI 复写
    ↓
批量发布到目标频道（图文保留，文案为清洗/AI 后版本）
```

- **图文保留**：`COPY_WITHOUT_FORWARD_TAG=true` 时用 `send_file` 复制媒体，不丢图。  
- **AI 只改字**：图片、视频、文件不变；只改写 `message` / `caption` 文字。  
- **广告拦截**：命中 `BLOCKED_KEYWORDS` 会标记为 `blocked`，不进入发布流程。  
- **数据持久化**：抓取数据存 SQLite（`data/posts.db`），重启不会丢。  
- **数据库校验**：网页「校验数据库/媒体」会去重、修复失效图片路径、删除孤儿文件。  
- **兼容接口**：`OPENAI_BASE_URL` 可填 OpenAI、DeepSeek、国内中转等 Chat Completions 兼容地址。  

## 权限要求

| 项目 | 要求 |
|------|------|
| 源频道 | 账号已加入该频道 |
| 目标频道 | 账号为管理员，可发消息 |
| API | [my.telegram.org](https://my.telegram.org) 的 api_id + api_hash |
| AI | `OPENAI_API_KEY`（开启 AI 时） |

## 配置项

| 变量 | 说明 |
|------|------|
| `SOURCE_CHANNELS` | 源频道，逗号分隔 `@a,@b` |
| `TARGET_CHANNEL` | 你的频道 `@my_channel` |
| `AI_ENABLED` | 是否 AI 复写 |
| `BLOCKED_KEYWORDS` | 广告屏蔽词（逗号分隔） |
| `STRIP_SOURCE_REFS` | 删除源站相关内容 |
| `DAILY_FETCH_*` | 每天定时抓取 |
| `BOT_*` | 抓取/发布结果 TG Bot 推送 |
| `OPENAI_*` | Key、Base URL、模型名 |
| `FILTER_KEYWORDS` | 只转发含关键词的消息 |

网页保存的配置写入 `settings.json`；`.env` 中的同名项会覆盖。

## 安全提示

- 勿泄露 `user.session` 和 `.env`  
- 网页默认只监听 `127.0.0.1`，不要直接暴露到公网  

## 后台常驻

```bash
nohup python web_app.py >> web.log 2>&1 &
```

## 服务器部署建议

- 用 `systemd` 或 Docker 长驻 `web_app.py`。  
- 国内服务器必须配置 `TELEGRAM_PROXY`。  
- 建议通过 SSH 隧道访问网页端，不直接暴露公网。  
