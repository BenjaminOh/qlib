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

import json
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


# ─── Formatting helpers (Korean, detailed per-order blocks) ─────────


def _won(v: float | None) -> str:
    return f"{round(v):,}" if v is not None else "—"


def _esc(s: str | None) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _order_basis(order) -> str | None:
    """Human sentence for why this order was placed (from reasons_json)."""
    try:
        why = json.loads(order.reasons_json) if order.reasons_json else {}
    except Exception:  # noqa: BLE001
        return None
    basis = why.get("basis") or ""
    summary = why.get("summary") or ""
    text = " — ".join(p for p in (basis, summary) if p)
    return text[:140] or None


def _side_tag(side: str) -> str:
    return "🔴 매수" if side == "BUY" else "🔵 매도"


STRATEGY_TITLES = {"open": "실계좌(KIS 모의투자)"}


def notify_open_orders(result: dict) -> None:
    """09:00 — per-order detail blocks read back from today's Order rows."""
    if not telegram_enabled() or result.get("status") == "no_signal":
        return
    as_of = result.get("as_of", "")
    strategy = result.get("strategy", "open")
    head = (f"🌅 <b>qlib 아침 주문</b> ({as_of}) · "
            f"{STRATEGY_TITLES.get(strategy, strategy)}")
    lines = [head]
    try:
        from datetime import date as _date

        from ..db import Order, SessionLocal
        with SessionLocal() as db:
            rows = (db.query(Order)
                      .filter(Order.trade_date == _date.fromisoformat(as_of),
                              Order.strategy == strategy)
                      .order_by(Order.side.desc(), Order.submitted_at.asc())
                      .all())
        for o in rows:
            ord_type = "지정가" if o.price else "시장가 (개장 동시호가)"
            status = ("✅ 접수" if o.status in ("SUBMITTED", "FILLED", "PARTIAL")
                      else f"❌ 거부: {_esc((o.error or '')[:80])}")
            lines.append("──────────────")
            lines.append(f"{_side_tag(o.side)} · <b>{_esc(o.name or o.code)}</b> ({o.code})")
            detail = f"📊 {ord_type} · {o.qty:,}주"
            if o.price:
                detail += f" @ {_won(o.price)}원"
            lines.append(detail)
            lines.append(f"📌 상태: {status}")
            basis = _order_basis(o)
            if basis:
                lines.append(f"📝 사유: {_esc(basis)}")
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_open_orders detail failed: %s", exc)
        lines.append(f"제출 {result.get('submitted', 0)} · 거부 {result.get('rejected', 0)}")
    lines.append("──────────────")
    tail = f"제출 {result.get('submitted', 0)} · 거부 {result.get('rejected', 0)}"
    skipped = result.get("skipped_expensive") or []
    if skipped:
        tail += f" · 고가주 스킵 {len(skipped)}"
    lines.append(tail)
    if result.get("rejected"):
        lines.append("⚠️ 거부 발생 — 대시보드 확인 필요")
    lines.append("⏰ 체결가·실현손익은 09:20 대사 후 다시 알려드립니다.")
    send_telegram("\n".join(lines))


def notify_order_detail(side: str, code: str, name: str | None, qty: int,
                        ok: bool, error: str | None = None) -> None:
    """Per-order line for the open strategy (fired as each order lands)."""
    if not telegram_enabled():
        return
    status = "접수" if ok else f"❌ 거부: {(error or '')[:80]}"
    send_telegram(f"{_side_tag(side)} {name or code} {qty:,}주 — {status}")


def notify_bracket_exits(strategy: str, result: dict) -> None:
    """15:48 sim exit detail — only when something actually sold."""
    exits = result.get("exits") or []
    if not telegram_enabled() or not exits:
        return
    lines = [f"📤 <b>{strategy} 시뮬 청산</b> ({result.get('trade_date', '')})"]
    for e in exits:
        realised = e.get("realised") or 0
        sign = "+" if realised >= 0 else ""
        icon = "💰" if realised >= 0 else "💸"
        lines.append("──────────────")
        lines.append(f"{icon} <b>{_esc(e.get('name') or e['code'])}</b> ({e['code']}) — {e['kind']}")
        lines.append(f"📊 매도 {e['qty']:,}주 @ {_won(e['price'])}원")
        lines.append(f"💵 실현손익: {sign}{_won(realised)}원")
        if e.get("sl_px"):
            lines.append(f"🛡 손절 기준: {_won(e['sl_px'])}원 ({e.get('sl_kind', '')})")
    send_telegram("\n".join(lines))


def notify_reconcile(summary: dict) -> None:
    """09:20 — actual fill prices + realised pnl per order."""
    if not telegram_enabled():
        return
    pinned = summary.get("pinned") or summary.get("updated") or 0
    if not pinned:
        return
    lines = [f"✅ <b>체결 확정</b> ({summary.get('trade_date', '')} 09:20 대사)"]
    try:
        from datetime import date as _date

        from ..db import Fill, Order, SessionLocal
        td = _date.fromisoformat(summary["trade_date"]) if summary.get("trade_date") else _date.today()
        with SessionLocal() as db:
            rows = (db.query(Order, Fill)
                      .join(Fill, Fill.order_id == Order.id)
                      .filter(Order.trade_date == td,
                              Order.strategy == "open",
                              Order.status.in_(("FILLED", "PARTIAL")))
                      .order_by(Order.side.desc())
                      .all())
        for o, f in rows:
            total = round((f.price or 0) * f.qty)
            line = (f"{_side_tag(o.side)} <b>{_esc(o.name or o.code)}</b> "
                    f"{f.qty:,}주 @ {_won(f.price)}원 = {total:,}원")
            if o.side == "SELL" and f.pnl is not None:
                sign = "+" if f.pnl >= 0 else ""
                line += f" → 실현 {sign}{_won(f.pnl)}원"
            lines.append(line)
        if not rows:
            lines.append(f"확정 {pinned}건")
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_reconcile detail failed: %s", exc)
        lines.append(f"확정 {pinned}건")
    send_telegram("\n".join(lines))
