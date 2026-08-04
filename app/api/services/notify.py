"""Telegram trade notifications — best-effort, never blocks trading.

Reuses the user's existing BotFather bot ("trading") and the
시스템트레이딩알림 group. Configure via env:

  QLIB_API_TELEGRAM_BOT_TOKEN  — BotFather token of the 'trading' bot
  QLIB_API_TELEGRAM_CHAT_ID    — the group's chat id (discover once with
                                 discover_chat_id() after the bot has seen
                                 any recent group message)

Every send is fire-and-forget: failures are logged and swallowed — a dead
Telegram must never fail an order task.
"""

from __future__ import annotations

import logging

import requests

from ..config import settings

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"


def telegram_enabled() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def send_telegram(text: str) -> bool:
    """Send `text` (Telegram HTML) to the configured chat. Best-effort."""
    if not telegram_enabled():
        return False
    try:
        r = requests.post(
            _API.format(token=settings.telegram_bot_token, method="sendMessage"),
            json={"chat_id": settings.telegram_chat_id, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=5)
        ok = r.status_code == 200 and (r.json() or {}).get("ok") is True
        if not ok:
            log.warning("telegram send failed HTTP %s: %s", r.status_code, r.text[:200])
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram send error: %s", exc)
        return False


def discover_chat_id() -> list[dict]:
    """One-shot helper: list chats the bot can currently see (getUpdates).

    Run manually after sending any message in the target group — returns
    [{chat_id, title, type}] so the right id can be copied into the env."""
    if not settings.telegram_bot_token:
        return []
    try:
        r = requests.get(
            _API.format(token=settings.telegram_bot_token, method="getUpdates"),
            timeout=10)
        seen: dict[int, dict] = {}
        for u in (r.json() or {}).get("result", []):
            msg = u.get("message") or u.get("my_chat_member") or {}
            chat = msg.get("chat") or {}
            if chat.get("id"):
                seen[chat["id"]] = {"chat_id": chat["id"],
                                    "title": chat.get("title") or chat.get("username"),
                                    "type": chat.get("type")}
        return list(seen.values())
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram discover error: %s", exc)
        return []


# ─── Formatting helpers (Korean, compact) ───────────────────────────


def _won(v: float | None) -> str:
    return f"{round(v):,}" if v is not None else "—"


def notify_open_orders(result: dict) -> None:
    """09:00 open-strategy order summary."""
    if not telegram_enabled() or result.get("status") == "no_signal":
        return
    lines = [f"🌅 <b>qlib 아침 주문</b> ({result.get('as_of', '')})"]
    lines.append(f"제출 {result.get('submitted', 0)} · 거부 {result.get('rejected', 0)}")
    skipped = result.get("skipped_expensive") or []
    if skipped:
        lines.append(f"고가주 스킵: {', '.join(skipped)}")
    if result.get("rejected"):
        lines.append("⚠️ 거부 발생 — 대시보드 확인 필요")
    send_telegram("\n".join(lines))


def notify_order_detail(side: str, code: str, name: str | None, qty: int,
                        ok: bool, error: str | None = None) -> None:
    """Per-order line for the open strategy (fired as each order lands)."""
    if not telegram_enabled():
        return
    icon = "🔴" if side == "BUY" else "🔵"
    status = "접수" if ok else f"❌ 거부: {(error or '')[:80]}"
    send_telegram(f"{icon} {side == 'BUY' and '매수' or '매도'} "
                  f"{name or code} {qty:,}주 — {status}")


def notify_bracket_exits(strategy: str, result: dict) -> None:
    """15:48 sim exit summary — only when something actually sold."""
    exits = result.get("exits") or []
    if not telegram_enabled() or not exits:
        return
    lines = [f"📤 <b>{strategy} 시뮬 청산</b> ({result.get('trade_date', '')})"]
    for e in exits:
        sign = "+" if e.get("realised", 0) >= 0 else ""
        lines.append(f"· {e['code']} {e['kind']} {e['qty']:,}주 @{_won(e['price'])} "
                     f"→ {sign}{_won(e.get('realised'))}원")
    send_telegram("\n".join(lines))


def notify_reconcile(summary: dict) -> None:
    """09:20 — fills pinned; only notify when orders exist today."""
    if not telegram_enabled():
        return
    pinned = summary.get("pinned") or summary.get("updated") or 0
    if not pinned:
        return
    send_telegram(f"✅ 체결가 확정 완료 — {pinned}건 (09:20 대사)")
