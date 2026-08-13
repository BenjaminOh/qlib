"use client";

import { useQuery } from "@tanstack/react-query";
import { api, LiveOrderRow, OrderStory, fmtDateTime, parseUtc } from "@/lib/api";
import { MetricBadges, FeatureContribList } from "@/components/ReasonBadges";

const num = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: digits });

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] font-semibold text-gray-500 mb-1">{title}</div>
      {children}
    </div>
  );
}

/** Rule lines vs the day's bar — the visual core of a bracket-exit story. */
function RuleLines({ story }: { story: OrderStory }) {
  const rules = story.rules;
  if (!rules || rules.exit_model === "rank_dropout" || rules.lines.length === 0) return null;
  return (
    <Section title="청산 규칙 선">
      <div className="space-y-1">
        {rules.lines.map((l, i) => {
          const isProfit = l.kind === "tp" || l.kind === "ladder_rung";
          return (
            <div key={i} className="flex items-center gap-2 text-xs">
              <span
                className={`inline-block w-2 h-2 rounded-full shrink-0 ${
                  isProfit ? "bg-emerald-400" : "bg-red-400"
                }`}
              />
              <span className="text-gray-700">{l.label}</span>
              <span className="font-mono text-gray-900">{num(l.px)}원</span>
              {l.hit != null && (
                <span
                  className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    l.hit
                      ? isProfit
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-red-100 text-red-700"
                      : "bg-gray-100 text-gray-500"
                  }`}
                >
                  {l.hit ? "당일 봉 통과" : "미도달"}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {rules.reconstructed && (
        <p className="text-[10px] text-amber-600 mt-1">
          ⚠ 판정 당시 기록이 없어 현행 규칙 기준으로 재구성한 추정치입니다
        </p>
      )}
    </Section>
  );
}

function DayBar({ story }: { story: OrderStory }) {
  const bar = story.bar;
  if (!bar) return null;
  const srcLabel = { recorded: "판정 시 기록", qlib: "kr_data", kis: "KIS 일봉" }[bar.source];
  return (
    <Section title={`당일 봉 (${story.order.trade_date})`}>
      <div className="grid grid-cols-4 gap-2 text-xs font-mono max-w-xs">
        {([["시가", bar.open], ["고가", bar.high], ["저가", bar.low], ["종가", bar.close]] as const).map(
          ([label, v]) => (
            <div key={label} className="bg-gray-50 rounded px-2 py-1 text-center">
              <div className="text-[10px] text-gray-400 font-sans">{label}</div>
              <div className="text-gray-900">{num(v)}</div>
            </div>
          ),
        )}
      </div>
      <p className="text-[10px] text-gray-400 mt-1">출처: {srcLabel}</p>
    </Section>
  );
}

function RankHistory({ story }: { story: OrderStory }) {
  if (story.rank_history.length === 0) return null;
  return (
    <Section title={`신호 순위 이력 (top-${story.topk} 유지가 보유 조건 · ${story.rank_store_n}위까지 기록)`}>
      <div className="flex flex-wrap gap-1">
        {story.rank_history.map((p) => {
          const inTop = p.rank != null && p.rank <= story.topk;
          return (
            <div
              key={p.as_of}
              className={`px-1.5 py-1 rounded text-center text-[10px] font-mono ${
                inTop
                  ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                  : p.rank != null
                    ? "bg-amber-50 text-amber-700 border border-amber-200"
                    : "bg-gray-50 text-gray-400 border border-gray-200"
              }`}
              title={p.as_of}
            >
              <div>{p.as_of.slice(5)}</div>
              <div className="font-semibold">{p.rank != null ? `${p.rank}위` : "권외"}</div>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

export default function OrderStoryPanel({ order }: { order: LiveOrderRow }) {
  const q = useQuery({
    queryKey: ["order-story", order.id],
    queryFn: () => api.getOrderStory(order.id),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  if (q.isLoading)
    return <div className="text-xs text-gray-400 py-3 px-1">스토리 불러오는 중…</div>;
  if (q.isError || !q.data)
    return <div className="text-xs text-red-500 py-3 px-1">스토리를 불러오지 못했습니다</div>;

  const s = q.data;
  const isSell = s.order.side === "SELL";
  const entryReasons = s.entry?.reasons ?? null;
  const sellMetrics = isSell && s.reasons?.metrics ? s.reasons.metrics : null;

  return (
    <div className="bg-gray-50/70 rounded-md px-3 py-3 space-y-3 text-left">
      {/* 이 주문의 판단 근거 한 줄 */}
      {s.order.basis && (
        <p className="text-xs text-gray-800">
          <span className="font-semibold">{isSell ? "매도 근거" : "매수 근거"}:</span>{" "}
          {s.order.basis}
        </p>
      )}

      {/* 진입 맥락 (매도 스토리) */}
      {isSell && s.entry && (
        <Section title="진입 맥락">
          <p className="text-xs text-gray-700">
            {s.entry.trade_date} 매수 {num(s.entry.qty)}주 @ {num(s.entry.exec_price)}원
            {s.entry.avg_at_sale != null && s.entry.avg_at_sale !== s.entry.exec_price && (
              <> · 매도 시점 평단 {num(s.entry.avg_at_sale)}원</>
            )}
            {s.entry.rank != null && <> · 진입일 신호 {s.entry.rank}위</>}
          </p>
          {s.entry.basis && <p className="text-[11px] text-gray-500 mt-0.5">{s.entry.basis}</p>}
          {entryReasons?.metrics && (
            <div className="mt-1">
              <MetricBadges m={entryReasons.metrics} />
            </div>
          )}
          {entryReasons?.top_features && entryReasons.top_features.length > 0 && (
            <div className="mt-1">
              <FeatureContribList features={entryReasons.top_features} />
            </div>
          )}
        </Section>
      )}

      {/* 매수 스토리: 자기 자신의 지표 */}
      {!isSell && s.reasons?.metrics && (
        <Section title="매수 시점 지표">
          <MetricBadges m={s.reasons.metrics} />
          {s.reasons.top_features && s.reasons.top_features.length > 0 && (
            <div className="mt-1">
              <FeatureContribList features={s.reasons.top_features} />
            </div>
          )}
        </Section>
      )}

      {/* 지정가 예약 상세 */}
      {s.limit_entry && (
        <Section title="지정가 예약 매수">
          <p className="text-xs text-gray-700">
            전일종가 {num(s.limit_entry.prev_close)}원 → 예약가 {num(s.limit_entry.limit_px)}원
            {s.limit_entry.fill_px != null && <> → 체결 {num(s.limit_entry.fill_px)}원</>}
            {s.limit_entry.discount_pct != null && (
              <span className={s.limit_entry.discount_pct < 0 ? "text-blue-600" : ""}>
                {" "}
                ({s.limit_entry.discount_pct.toFixed(1)}% vs 전일)
              </span>
            )}
          </p>
          {s.limit_entry.gap_down_fill && (
            <p className="text-[11px] text-blue-600 mt-0.5">
              갭하락 시가 체결 — 예약가보다 유리한 가격에 담겼습니다
            </p>
          )}
        </Section>
      )}

      <RuleLines story={s} />
      <DayBar story={s} />
      {(s.order.strategy === "open" || s.rules?.exit_model === "rank_dropout") && (
        <RankHistory story={s} />
      )}

      {/* 매도 시점 지표 스냅샷 */}
      {sellMetrics && (
        <Section title="매도 시점 지표">
          <MetricBadges m={sellMetrics} />
        </Section>
      )}

      {/* 결과 · 이후 흐름 */}
      {isSell && (
        <Section title="결과">
          <p className="text-xs text-gray-700">
            {s.stage != null && <>사다리 {s.stage}차 매도 · </>}
            {s.position_before != null && s.position_after != null && (
              <>
                보유 {num(s.position_before)}주 → {num(s.position_after)}주
                {s.position_after > 0 ? " (부분 매도)" : " (전량 청산)"} ·{" "}
              </>
            )}
            {s.order.realized_pnl != null && (
              <span
                className={`font-mono font-semibold ${
                  s.order.realized_pnl >= 0 ? "text-emerald-700" : "text-red-700"
                }`}
              >
                실현 {s.order.realized_pnl >= 0 ? "+" : ""}
                {num(Math.round(s.order.realized_pnl))}원
              </span>
            )}
          </p>
          {s.post_closes.length > 0 && (
            <p className="text-[11px] text-gray-500 mt-1">
              매도 후 종가:{" "}
              {s.post_closes.map((p) => `${p.trade_date.slice(5)} ${num(p.close)}`).join(" · ")}
              {s.give_back_pct != null && (
                <span
                  className={`ml-1 font-mono ${
                    s.give_back_pct < 0 ? "text-emerald-700" : "text-red-600"
                  }`}
                  title="매도가 대비 이후 종가 등락 — 음수면 매도 후 더 내려간 것(잘 판 것), 양수면 매도 후 더 오른 것"
                >
                  ({s.give_back_pct >= 0 ? "+" : ""}
                  {s.give_back_pct.toFixed(1)}% vs 매도가)
                </span>
              )}
            </p>
          )}
        </Section>
      )}
      {!isSell && s.post_closes.length > 0 && (
        <Section title="매수 후 흐름">
          <p className="text-[11px] text-gray-500">
            매수 후 종가:{" "}
            {s.post_closes.map((p) => `${p.trade_date.slice(5)} ${num(p.close)}`).join(" · ")}
          </p>
        </Section>
      )}

      {/* 판정 방식 */}
      <p className="text-[11px] text-gray-500 border-t border-gray-200 pt-2">
        {s.judgment.text}
        {s.judgment.recorded_at && (
          <span className="text-gray-400"> — {fmtDateTime(parseUtc(s.judgment.recorded_at))} 기록</span>
        )}
      </p>

      {s.notes.length > 0 && (
        <ul className="text-[10px] text-gray-400 space-y-0.5">
          {s.notes.map((n, i) => (
            <li key={i}>· {n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
