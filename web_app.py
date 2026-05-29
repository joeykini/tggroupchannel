#!/usr/bin/env python3
"""网页控制台：抓取、瀑布流待发布池、批量发布、定时任务。"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from time import monotonic
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bridge import ChannelBridge, MEDIA_DIR
from config import Settings, load_settings, save_settings
from post_store import (
    delete_posts_and_media,
    list_pending_ids,
    list_posts,
    validate_and_cleanup,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
_log_buffer: deque[dict[str, str]] = deque(maxlen=300)
_bridge = ChannelBridge(on_log=lambda level, msg: _log_buffer.append({"level": level, "message": msg}))
_login_state: dict[str, Any] = {}
_scheduler_task: asyncio.Task | None = None
_last_daily_run: str = ""
_login_status_cache: dict[str, Any] = {"value": False, "ts": 0.0}
_LOGIN_STATUS_TTL_SECONDS = 20.0


def _format_tg_error(exc: BaseException) -> str:
    msg = str(exc) or type(exc).__name__
    lower = msg.lower()
    if "api_id/api_hash combination is invalid" in lower or "api id invalid" in lower:
        return (
            "API_ID / API_HASH 无效或不匹配。请到 my.telegram.org 重新复制一对，"
            "先点“保存配置”，再重新“发送验证码”。"
        )
    if "phone code" in lower and "expired" in lower:
        return "验证码已过期，请重新点击“发送验证码”。"
    if "phone code hash" in lower:
        return "验证码会话无效，请重新点击“发送验证码”。"
    if "Connection to Telegram failed" in msg or "Timeout" in msg:
        return (
            "无法连接 Telegram 服务器。请开启 VPN/代理并配置 TELEGRAM_PROXY，"
            "例如 socks5://127.0.0.1:7890。"
        )
    return msg


def _clear_login_cache() -> None:
    _login_status_cache["ts"] = 0.0


async def _get_cached_logged_in(force: bool = False) -> bool:
    if not force and monotonic() - float(_login_status_cache["ts"]) < _LOGIN_STATUS_TTL_SECONDS:
        return bool(_login_status_cache["value"])
    try:
        value = await _bridge.is_logged_in()
    except Exception:
        value = False
    _login_status_cache["value"] = value
    _login_status_cache["ts"] = monotonic()
    return value


class ConfigBody(BaseModel):
    api_id: int | None = None
    api_hash: str | None = None
    session_name: str | None = None
    source_channels: str | None = None
    target_channel: str | None = None
    filter_keywords: str | None = None
    blocked_keywords: str | None = None
    forward_header: str | None = None
    copy_without_forward_tag: bool | None = None
    auto_publish: bool | None = None
    require_media: bool | None = None
    strip_source_refs: bool | None = None
    ai_enabled: bool | None = None
    ai_prompt: str | None = None
    template_extract_enabled: bool | None = None
    publish_template: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    telegram_proxy: str | None = None
    daily_fetch_enabled: bool | None = None
    daily_fetch_time: str | None = None
    daily_fetch_limit: int | None = None
    fetch_on_start: bool | None = None
    bot_enabled: bool | None = None
    bot_token: str | None = None
    bot_chat_id: str | None = None


class PhoneBody(BaseModel):
    phone: str


class CodeBody(BaseModel):
    phone: str
    code: str
    phone_code_hash: str
    password: str | None = None


class PublishBody(BaseModel):
    ids: list[str] = Field(default_factory=list)
    all_pending: bool = False


class DeleteBody(BaseModel):
    ids: list[str] = Field(default_factory=list)


class FetchBody(BaseModel):
    limit: int = 30
    since_hours: int = 0


async def _daily_scheduler_loop() -> None:
    global _last_daily_run
    while True:
        try:
            settings = load_settings()
            if settings.daily_fetch_enabled:
                now = datetime.now()
                hm = now.strftime("%H:%M")
                day_key = now.strftime("%Y-%m-%d")
                if hm == settings.daily_fetch_time and _last_daily_run != day_key:
                    _last_daily_run = day_key
                    _log_buffer.append({"level": "INFO", "message": "触发每日定时抓取"})
                    await _bridge.fetch_recent_once(limit_per_channel=settings.daily_fetch_limit)
        except Exception as e:
            _log_buffer.append({"level": "ERROR", "message": f"定时抓取失败: {e}"})
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_daily_scheduler_loop())
    yield
    if _scheduler_task:
        _scheduler_task.cancel()
    if _bridge.running:
        await _bridge.stop()


app = FastAPI(title="Telegram 频道转发", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


@app.middleware("http")
async def media_cache_headers(request: Request, call_next):
    response = await call_next(request)
    # 媒体文件名包含消息键，内容变更会生成新文件名，适合长缓存
    if request.url.path.startswith("/media/") and response.status_code in (200, 206, 304):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/fetch")
async def fetch_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "fetch.html")


@app.get("/api/status")
async def status() -> dict:
    logged_in = await _get_cached_logged_in()
    return {
        "logged_in": logged_in,
        "bridge_running": _bridge.running,
        "logs": list(_log_buffer),
        "post_count": len(list_posts(200)),
        "pending_count": len(list_pending_ids(9999)),
    }


@app.get("/api/posts")
async def api_posts(limit: int = 80, status: str | None = None) -> dict:
    return {"posts": list_posts(limit=limit, status=status)}


@app.get("/api/config")
async def get_config() -> dict:
    s = load_settings()
    d = s.to_dict()
    if d.get("openai_api_key"):
        d["openai_api_key"] = d["openai_api_key"][:8] + "..." if len(d["openai_api_key"]) > 8 else "***"
    if d.get("bot_token"):
        d["bot_token"] = d["bot_token"][:8] + "..." if len(d["bot_token"]) > 8 else "***"
    return d


@app.post("/api/config")
async def post_config(body: ConfigBody) -> dict:
    current = load_settings()
    data = current.to_dict()
    incoming = body.model_dump(exclude_none=True)
    for key, val in incoming.items():
        data[key] = val

    for masked_key in ("openai_api_key", "bot_token"):
        if masked_key in incoming:
            raw = str(incoming[masked_key]).strip()
            if not raw or "..." in raw:
                data[masked_key] = getattr(current, masked_key)
            else:
                data[masked_key] = raw

    settings = Settings.from_dict(data)
    save_settings(settings)
    _bridge.reload_settings()
    _clear_login_cache()
    return {"ok": True}


@app.post("/api/login/send_code")
async def login_send_code(body: PhoneBody) -> dict:
    s = load_settings()
    if not s.api_id or not s.api_hash:
        raise HTTPException(400, "请先填写 API_ID 和 API_HASH")
    try:
        result = await _bridge.login_send_code(body.phone.strip())
        _login_state["phone"] = body.phone.strip()
        _login_state["phone_code_hash"] = result["phone_code_hash"]
        _log_buffer.append({"level": "INFO", "message": f"验证码已发送到 {body.phone}"})
        _clear_login_cache()
        return {"ok": True, "phone_code_hash": result["phone_code_hash"]}
    except Exception as e:
        raise HTTPException(400, _format_tg_error(e)) from e


@app.post("/api/login/confirm")
async def login_confirm(body: CodeBody) -> dict:
    try:
        result = await _bridge.login_confirm(
            body.phone,
            body.code.strip(),
            body.phone_code_hash or _login_state.get("phone_code_hash", ""),
            body.password,
        )
        if result.get("need_2fa"):
            return {"need_2fa": True}
        _log_buffer.append({"level": "INFO", "message": f"登录成功: {result.get('user')}"})
        _clear_login_cache()
        return result
    except Exception as e:
        raise HTTPException(400, _format_tg_error(e)) from e


@app.post("/api/bridge/start")
async def bridge_start() -> dict:
    if _bridge.running:
        return {"ok": True, "message": "已在运行"}
    try:
        await _bridge.start()
        _clear_login_cache()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/bridge/stop")
async def bridge_stop() -> dict:
    await _bridge.stop()
    _clear_login_cache()
    return {"ok": True}


@app.post("/api/fetch/once")
async def fetch_once(body: FetchBody) -> dict:
    before = list(_log_buffer)
    try:
        task = asyncio.create_task(
            _bridge.fetch_recent_once(
                limit_per_channel=max(1, body.limit),
                since_hours=max(0, body.since_hours),
            )
        )
        result = await asyncio.shield(task)
        after = list(_log_buffer)
        new_logs = after[len(before) :] if len(after) >= len(before) else after
        return {
            "ok": True,
            **result,
            "debug_logs": new_logs,
            "has_error": any((x.get("level") or "").upper() == "ERROR" for x in new_logs),
        }
    except asyncio.CancelledError as e:
        raise HTTPException(503, "抓取任务被中断，请稍后重试") from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/posts/publish")
async def publish_posts(body: PublishBody) -> dict:
    try:
        if body.all_pending:
            result = await _bridge.publish_all_pending()
        else:
            result = await _bridge.publish_posts(body.ids)
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/posts/delete")
async def api_delete_posts(body: DeleteBody) -> dict:
    result = delete_posts_and_media(body.ids, MEDIA_DIR)
    return {"ok": True, **result}


@app.post("/api/maintenance/validate")
async def api_validate_db() -> dict:
    result = validate_and_cleanup(MEDIA_DIR)
    return {"ok": True, **result}


@app.post("/api/target/validate")
async def api_validate_target() -> dict:
    try:
        return await _bridge.validate_target_channel()
    except Exception as e:
        raise HTTPException(400, str(e)) from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_app:app", host="127.0.0.1", port=8765, reload=False)
