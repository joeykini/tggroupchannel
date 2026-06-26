# Telegram 频道抓取 · 模板提取 · 自动发布

从源频道抓取相册+配文，按模板提取字段后发布到目标频道。支持广告过滤、去源站信息、内容去重、源频道删帖同步。

## 快速开始（命令行）

```bash
cd ~/tggroupchannel          # 或你的项目目录
python3 -m venv .venv        # 注意是 .venv，不是 venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # 填写 API_ID、API_HASH、SOURCE_CHANNELS、TARGET_CHANNEL
python forwarder.py login
python forwarder.py run
```

## 服务器部署与后台运行

项目在服务器上默认使用 **`.venv`**（带点），激活命令是：

```bash
source .venv/bin/activate    # 不是 venv/bin/activate
```

### 首次部署

```bash
cd ~/tggroupchannel
git clone https://github.com/joeykini/tggroupchannel.git .   # 若尚未克隆

# Debian/Ubuntu 若缺少 venv 模块
apt update
apt install -y python3-venv python3-pip git

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env                      # 填写 API_ID、API_HASH、频道、BOT 等

python forwarder.py login      # 首次登录 Telegram
```

### 前台运行（调试）

```bash
cd ~/tggroupchannel
source .venv/bin/activate
python forwarder.py run
```

### 后台运行（推荐 nohup，无需 screen）

```bash
cd ~/tggroupchannel
source .venv/bin/activate

# 若已有旧进程，先停掉
pkill -f "forwarder.py run" 2>/dev/null

# 后台启动，日志写入 nohup.out
nohup python forwarder.py run > nohup.out 2>&1 &

# 查看是否在跑
ps aux | grep forwarder

# 实时看日志（Ctrl+C 只退出 tail，进程继续跑）
tail -f nohup.out
```

### 停止 / 更新 / 重启

```bash
cd ~/tggroupchannel

# 1. 停止
pkill -f "forwarder.py run"

# 2. 拉取最新代码
git pull

# 3. 激活环境（有依赖变更时再 pip install -r requirements.txt）
source .venv/bin/activate

# 4. （可选）立刻清理人员库：商k / 非淮安本地区
python forwarder.py roster

# 5. 重新后台启动
nohup python forwarder.py run > nohup.out 2>&1 &
tail -f nohup.out
```

### 可选：screen / systemd

未安装 screen 时可直接 `apt install -y screen`，或用 `tmux`。长期常驻也可配置 `systemd` 服务（见文末说明）。

## 命令

| 命令 | 说明 |
|------|------|
| `python forwarder.py login` | 命令行登录（首次必做） |
| `python forwarder.py run` | 监听源频道，自动抓取/模板提取/发布 |
| `python forwarder.py fetch --limit 30` | 立即补抓最近帖子 |
| `python forwarder.py sync` | 对比源频道删帖与内容重复 |
| `python forwarder.py roster` | 出勤名单同步：合并在岗、删不在岗（统一榜） |

## 出勤名单 + 统一榜（两组源）

| 组别 | 出勤群 | 源频道 |
|------|--------|--------|
| 1 | [@HuaiAnHub](https://t.me/HuaiAnHub) | [@huaian008](https://t.me/huaian008) |
| 2 | [@HuaiAn_YangZhou](https://t.me/HuaiAn_YangZhou) | [@huaian0901](https://t.me/huaian0901) |

统一发布到 [@huaianbendi](https://t.me/huaianbendi)：

- 🟢 在线 / 🔴 休息 **都在岗**（只要在出勤名单里就不删）
- 同一人跨频道 **合并资料**，写入人员库（**不自动发布**）
- 管理 Bot：**人员库** → 点名字预览 → 点发布
- 广告/无模板/非淮安本地区 **不入库**（`REGION_FILTER_ENABLED`、`ALLOWED_REGIONS`）
- 含「商k」等广告词 **不入库并自动清理**
- **不在出勤名单** 且已发布过 → 可选删统一榜帖（`DELETE_INACTIVE_FROM_TARGET=true`）

```bash
python forwarder.py roster   # 手动：群内触发「出勤」→ 解析 → 发布/删帖
```

## 流程

```
源频道新帖（相册 + 配文）
    ↓
字段识别（昵称/名字/价位/地区/频道/@id 等多别名）
    ↓
广告过滤 + 去源站痕迹 + 模板重排（AI 关闭时默认启用）
    ↓
内容指纹去重
    ↓
发布到目标频道（复制发送，不带「转发自」）
    ↓
定时 sync：检测源帖已删、补抓遗漏、标记重复
```

## 模板变量

`{name}` `{age}` `{height}` `{weight}` `{cup}` `{project}` `{price_once}` `{price_twice}` `{region}` `{telegram}` `{channel}` `{duplex}` 以及评分区 `{review_count}` `{good_rate}` `{photo_score}` 等。

源站即使用「昵称」「名字」「价位」「位置」等不同写法，也会映射到同一字段。

## 配置（.env）

| 变量 | 说明 |
|------|------|
| `SOURCE_CHANNELS` | 源频道，逗号分隔 |
| `TARGET_CHANNEL` | 目标频道 |
| `AI_ENABLED` | 关闭时按 `PUBLISH_TEMPLATE` 提取并重排 |
| `CONTENT_DEDUP_ENABLED` | 按内容指纹去重 |
| `SYNC_ENABLED` | 定时对比源频道删帖 |
| `SYNC_INTERVAL_MINUTES` | 同步间隔（分钟） |
| `DELETE_FROM_TARGET_ON_SOURCE_REMOVED` | 源删时同步删目标帖 |
| `BLOCKED_KEYWORDS` | 广告屏蔽词（含 `商k`） |
| `REGION_FILTER_ENABLED` | 仅允许本地区入库（默认开） |
| `ALLOWED_REGIONS` | 允许地区，逗号分隔（默认淮安 7 区县） |
| `ROSTER_SYNC_TIME` | 凌晨任务时间（默认 `02:30`） |
| `PUBLISH_INTERVAL_SECONDS` | 批量发布每条间隔（秒） |
| `AUTO_PUBLISH_AFTER_ROSTER` | 凌晨比对后是否自动发布（默认关） |
| `BOT_*` | 抓取/发布结果 TG Bot 推送 |
| `BOT_ADMIN_IDS` | 允许在 Bot 内改配置的管理员用户 ID |

`.env` 与 `settings.json` 均可配置，`settings.json` 优先。

## 管理 Bot（在 Telegram 内改参数）

配置 `BOT_TOKEN` 和 `BOT_ADMIN_IDS` 后，`python forwarder.py run` 会自动启动管理 Bot。

| 命令 | 说明 |
|------|------|
| `/menu` | 按钮面板（开关、同步、补抓） |
| `/status` | 查看当前配置 |
| `/toggle ai` | 切换 AI / 自动发布 / 同步 / 去重 等 |
| `/set source @a,@b` | 修改源频道 |
| `/set target @my_channel` | 修改目标频道 |
| `/set sync_interval 30` | 修改同步间隔（分钟） |

修改会写入 `settings.json` 并立即生效。仅 `BOT_ADMIN_IDS`（或 `BOT_CHAT_ID`）中的用户可操作。

获取你的用户 ID：给 [@userinfobot](https://t.me/userinfobot) 发消息即可。

## 权限

| 项目 | 要求 |
|------|------|
| 源频道 | 账号已加入 |
| 目标频道 | 账号为管理员，可发消息 |
| API | [my.telegram.org](https://my.telegram.org) |

## 安全

- 勿泄露 `user.session` 和 `.env`
- 服务器建议用 `nohup` 或 `systemd` 常驻 `python forwarder.py run`

### systemd 示例（可选）

```ini
# /etc/systemd/system/tg-forwarder.service
[Unit]
Description=Telegram channel forwarder
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/tggroupchannel
Environment=PATH=/root/tggroupchannel/.venv/bin
ExecStart=/root/tggroupchannel/.venv/bin/python forwarder.py run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable tg-forwarder
systemctl start tg-forwarder
systemctl status tg-forwarder
journalctl -u tg-forwarder -f
```
