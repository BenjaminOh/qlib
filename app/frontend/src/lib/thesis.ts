import { LiveSignalRow } from "@/lib/api";

/** Rule-based Korean buy thesis + composite score for the daily picks.
 *  Computed client-side from data already in /live/signals (metrics,
 *  top_features, all scores), so it applies retroactively to any signal. */

export interface Composite {
  /** 0–100, min-max scaled within the day's picks. */
  score: number;
  /** True when this pick's alpha ties with another pick — rank is then
   *  code-order fallback, not model conviction (degenerate-signal days). */
  tied: boolean;
}

export function compositeScores(picks: LiveSignalRow[]): Map<string, Composite> {
  const out = new Map<string, Composite>();
  const scores = picks.map((p) => p.score).filter((s): s is number => s != null);
  if (!scores.length) return out;
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const counts = new Map<number, number>();
  for (const s of scores) counts.set(s, (counts.get(s) ?? 0) + 1);
  for (const p of picks) {
    if (p.score == null) continue;
    const scaled = max > min ? ((p.score - min) / (max - min)) * 100 : 100;
    out.set(p.code, {
      score: Math.round(scaled),
      tied: (counts.get(p.score) ?? 0) > 1,
    });
  }
  return out;
}

/** 2–3 Korean sentences: [type diagnosis] + [model rationale] + [risk/confidence]. */
export function buildThesis(pick: LiveSignalRow, picks: LiveSignalRow[]): string | null {
  const m = pick.reasons?.metrics;
  if (!m) return null;
  const ret5 = m.ret5 ?? null;
  const ret20 = m.ret20 ?? null;
  const ma20 = m.ma20_gap ?? null;
  const high60 = m.high60_pos ?? null;
  const vol = m.vol_ratio ?? null;

  // ── Type diagnosis ──
  let type: string;
  if (high60 != null && high60 <= -30 && ret5 != null && ret5 > 0) {
    type = `60일 고점 대비 ${high60}% 낙폭 구간에서 최근 5일 ${ret5 > 0 ? "+" : ""}${ret5}% 반등이 시작된 **낙폭과대 반등형**`;
  } else if (ret5 != null && ret5 >= 15) {
    type = `최근 5일 +${ret5}%의 **급등 모멘텀 추종형**`;
  } else if (ret20 != null && ret20 >= 10 && ma20 != null && ma20 > 0) {
    type = `20일 +${ret20}% 상승 추세가 살아있는 **추세 지속형**`;
  } else if (ret20 != null && ret20 > 0 && ret5 != null && ret5 < 0) {
    type = `상승 추세(20일 +${ret20}%) 중 단기 눌림(5일 ${ret5}%) 자리의 **눌림 회복형**`;
  } else {
    type = "뚜렷한 방향성보다 전 종목 상대 평가에서 우위를 보인 **상대우위형**";
  }

  // ── Model rationale (top contributing features) ──
  const feats = (pick.reasons?.top_features ?? []).filter((f) => f.contrib > 0).slice(0, 2);
  const rationale = feats.length
    ? `모델이 가장 크게 반영한 지표는 ${feats.map((f) => f.desc).join("과 ")}${vol != null ? ` (거래량 ${vol}배)` : ""}.`
    : null;

  // ── Risk / confidence ──
  const notes: string[] = [];
  if (ret5 != null && ret5 >= 20) notes.push("⚠️ 과열 구간 — 시가 갭·되돌림 주의");
  if (high60 != null && high60 <= -40) notes.push("깊은 하락 추세의 반등 배팅 — 실패 시 신저가 위험");
  if (ma20 != null && ma20 < 0 && !(ret20 != null && ret20 > 0)) notes.push(`20일선 아래(${ma20}%)라 중기 추세는 아직 하락 우위`);
  const comp = compositeScores(picks).get(pick.code);
  if (comp?.tied) {
    notes.push("⚠️ 모델 동점 구간 — 이 순위 자체는 변별력이 없음");
  } else if (pick.rank === 1 && picks.length > 1) {
    const second = picks.find((p) => p.rank === 2)?.score;
    if (pick.score != null && second != null && second > 0 && pick.score / second >= 2) {
      notes.push("2위 대비 점수 2배 이상 — 모델 확신이 특히 강한 픽");
    }
  }

  const parts = [`${type}으로 매수.`];
  if (rationale) parts.push(rationale);
  if (notes.length) parts.push(notes.join(" · ") + ".");
  return parts.join(" ");
}

/** Render **bold** markers from buildThesis as <strong>. */
export function thesisSegments(text: string): { bold: boolean; t: string }[] {
  return text.split("**").map((t, i) => ({ bold: i % 2 === 1, t }));
}
