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


def _chart(code: str, name: str | None = None, bold: bool = False) -> str:
    """Stock name as a TradingView chart link (KRX:code, KOSPI+KOSDAQ)."""
    label = _esc(name or code)
    if bold:
        label = f"<b>{label}</b>"
    return f'<a href="https://kr.tradingview.com/chart/?symbol=KRX%3A{code}">{label}</a>'


def _side_tag(side: str) -> str:
    return "🔴 매수" if side == "BUY" else "🔵 매도"


STRATEGY_TITLES = {"open": "실계좌(KIS 모의투자)"}
_DIV = "━━━━━━━━━━━━━━"


def notify_open_orders(result: dict) -> None:
    """09:00 — per-order detail blocks read back from today's Order rows."""
    if not telegram_enabled() or result.get("status") == "no_signal":
        return
    as_of = result.get("as_of", "")
    strategy = result.get("strategy", "open")
    head = (f"🌅 <b>qlib 아침 주문</b> ({as_of}) · "
            f"{STRATEGY_TITLES.get(strategy, strategy)}")
    lines = [head, ""]
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
            ord_type = "지정가" if o.price else "시장가"
            status = ("✅ 접수" if o.status in ("SUBMITTED", "FILLED", "PARTIAL")
                      else f"❌ 거부: {_esc((o.error or '')[:80])}")
            detail = f"📊 {ord_type} · {o.qty:,}주"
            if o.price:
                detail += f" @ {_won(o.price)}원"
            block = [_DIV, "",
                     f"{_side_tag(o.side)} · {_chart(o.code, o.name, bold=True)} ({o.code})",
                     "",
                     detail,
                     f"📌 상태: {status}"]
            basis = _order_basis(o)
            if basis:
                block.append(f"📝 {_esc(basis)}")
            block.append("")
            lines.extend(block)
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_open_orders detail failed: %s", exc)
        lines.append(f"제출 {result.get('submitted', 0)} · 거부 {result.get('rejected', 0)}")
    lines.extend([_DIV, ""])
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
    lines = [f"📤 <b>{strategy} 시뮬 청산</b> ({result.get('trade_date', '')})", ""]
    for e in exits:
        realised = e.get("realised") or 0
        sign = "+" if realised >= 0 else ""
        icon = "💰" if realised >= 0 else "💸"
        lines.extend([
            _DIV, "",
            f"{icon} {_chart(e['code'], e.get('name'), bold=True)} ({e['code']}) — {e['kind']}",
            "",
            f"📊 매도 {e['qty']:,}주 @ {_won(e['price'])}원",
            f"💵 실현손익: {sign}{_won(realised)}원",
        ])
        if e.get("sl_px"):
            lines.append(f"🛡 손절 기준: {_won(e['sl_px'])}원 ({e.get('sl_kind', '')})")
        lines.append("")
    send_telegram("\n".join(lines))


def notify_reconcile(summary: dict) -> None:
    """09:20 — actual fill prices + realised pnl per order."""
    if not telegram_enabled():
        return
    pinned = summary.get("pinned") or summary.get("updated") or 0
    if not pinned:
        return
    lines = [f"✅ <b>체결 확정</b> ({summary.get('trade_date', '')} 09:20 대사)", ""]
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
            lines.extend([
                f"{_side_tag(o.side)} {_chart(o.code, o.name, bold=True)}",
                f"💵 {f.qty:,}주 @ {_won(f.price)}원 = {total:,}원",
            ])
            if o.side == "SELL" and f.pnl is not None:
                sign = "+" if f.pnl >= 0 else ""
                lines.append(f"💰 실현손익: {sign}{_won(f.pnl)}원")
            lines.append("")
        if not rows:
            lines.append(f"확정 {pinned}건")
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_reconcile detail failed: %s", exc)
        lines.append(f"확정 {pinned}건")
    send_telegram("\n".join(lines))


_SCOUT_PATTERNS = {"B": "급등 타이트 눌림", "A": "신고가 돌파", "R": "저항대 돌파",
                   "C": "급등 눌림 재진입", "D": "낙폭과대 반등"}


def notify_scout(result: dict, *, slot_label: str, final: bool = False) -> None:
    """14:30/15:00 scout scans + 15:05 regular screen — candidate list.

    Scouts are observation-only; the 15:05 message marks the picks that the
    15:28 slot will actually sim-buy."""
    if not telegram_enabled() or result.get("status") != "ok":
        return
    picks = result.get("picks") or []
    icon = "☕" if final else "🔭"
    kind = "정규 스크린" if final else "정찰 스캔 (관측 전용)"
    lines = [f"{icon} <b>카페 {kind} {slot_label}</b> ({result.get('trade_date', '')})", ""]
    if not picks:
        lines.append("매치된 후보 없음 (풀 "
                     f"{result.get('pool', 0)}종목 스캔)")
    for i, p in enumerate(picks, start=1):
        label = _SCOUT_PATTERNS.get(p.get("pattern"), p.get("pattern", ""))
        px = p.get("price") or p.get("close")
        line = (f"{i}) {_chart(p.get('code', ''), p.get('name'))} · "
                f"{p.get('pattern')} {label} · {_won(px)}원")
        if final and p.get("stop_px") is not None:
            line += f" · 손절 {_won(p['stop_px'])}원"
        lines.append(line)
    lines.append("")
    if final:
        lines.append(f"상위 {min(len(picks), 2)}종목을 15:28에 시뮬 매수합니다.")
    else:
        lines.append("매매 없음 — 15:05 정규 스크린·종가와 비교용 기록입니다.")
    send_telegram("\n".join(lines))


def notify_surge_picks(result: dict) -> None:
    """15:12 — surge-eve TOP10 (연구 프로파일 점수순, 상위 2 시뮬 매수 예고)."""
    if not telegram_enabled() or result.get("status") != "ok":
        return
    picks = result.get("picks") or []
    lines = [f"⚡ <b>급등 전야 후보 TOP10</b> ({result.get('trade_date', '')})", ""]
    if not picks:
        lines.append(f"프로파일 통과 종목 없음 (풀 {result.get('pool', 0)}종목)")
    for p in picks:
        lines.append(f"{p['rank']}) {_chart(p['code'], p.get('name'))} · "
                     f"{_won(p.get('close'))}원 · {p['score']}점")
    lines.append("")
    lines.append("추세+눌림+거래량 프로파일(3.5년 2,356건 학습) 점수순 — "
                 f"상위 {min(len(picks), 2)}종목을 15:29에 시뮬 매수합니다.")
    send_telegram("\n".join(lines))


def notify_signal(result: dict) -> None:
    """Evening — next-day recommended picks: 종목·종가·종합점수 한 줄씩.

    live_signal fires from the refresh chain AND a 16:20 fallback beat, so a
    redis NX key dedupes per as_of (best-effort: no redis → send anyway)."""
    if not telegram_enabled():
        return
    as_of = result.get("as_of")
    if not as_of:
        return
    try:
        import redis  # type: ignore[import-not-found]
        r = redis.from_url(settings.celery_broker_url)
        if not r.set(f"qlib:tg:signal:{as_of}", "1", nx=True, ex=86400):
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        from collections import Counter
        from datetime import date as _date

        from ..db import SessionLocal, Signal
        from .live_trader import _last_close
        with SessionLocal() as db:
            rows = (db.query(Signal)
                      .filter(Signal.as_of == _date.fromisoformat(as_of),
                              Signal.rank <= 10)
                      .order_by(Signal.rank.asc())
                      .all())
        if not rows:
            return
        scores = [s.score for s in rows if s.score is not None]
        mn, mx = min(scores), max(scores)
        dup = {k for k, v in Counter(scores).items() if v > 1}
        lines = [f"📌 <b>다음 거래일 추천 종목 TOP10</b> ({as_of})", ""]
        for s in rows:
            pct = None
            try:
                pct = (json.loads(s.reasons_json) or {}).get("universe_pct") if s.reasons_json else None
            except Exception:  # noqa: BLE001
                pass
            if s.score in dup:
                tag = "동점"
            elif pct is not None:
                tag = f"상위 {pct}%"
            elif mx > mn and s.score is not None:
                tag = f"{round((s.score - mn) / (mx - mn) * 100)}점"
            else:
                tag = "—"
            px = _last_close(s.code)
            px_txt = f"{round(px):,}원" if px else "—"
            lines.append(f"{s.rank}) {_chart(s.code, s.name)} · {px_txt} · {tag}")
        n_dup = sum(1 for s in rows if s.score in dup)
        if n_dup:
            lines.extend(["", f"⚠️ 동점 {n_dup}종목 — 해당 순위는 모델 변별력 없음"])
        lines.extend(["", "내일 15:20 시뮬 매수 · 익일 09:00 실주문에 사용됩니다."])
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_signal failed: %s", exc)
        return
    send_telegram("\n".join(lines))
