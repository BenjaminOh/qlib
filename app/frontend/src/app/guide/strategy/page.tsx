/**
 * /guide/strategy — 매수 전략 설명서.
 *
 * The complete buy-decision pipeline as actually implemented in
 * live_trader.py, in plain Korean with the real 2026-07-29 first-buy
 * worked example. Static page, no API calls.
 */
import Link from "next/link";

const Step = ({ n, title, children }: { n: string; title: string; children: React.ReactNode }) => (
  <section className="border border-gray-200 rounded-lg p-5 bg-white">
    <h2 className="text-xl font-bold text-gray-900 mb-3">
      <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-blue-600 text-white text-sm mr-2">{n}</span>
      {title}
    </h2>
    {children}
  </section>
);

export default function StrategyGuidePage() {
  return (
    <article className="max-w-none space-y-6">
      <header className="border-b border-gray-200 pb-6">
        <h1 className="text-3xl font-bold text-gray-900">📐 매수 전략 설명서</h1>
        <p className="mt-2 text-gray-600">
          이 시스템이 <strong>어떤 기준으로 종목을 사는지</strong> — 후보 선정부터 주문까지 전 과정을
          실제 코드에 구현된 규칙 그대로 설명합니다.
        </p>
      </header>

      {/* 30초 요약 */}
      <section className="bg-blue-50 border border-blue-200 rounded-lg p-5">
        <h2 className="text-lg font-semibold text-blue-900 mb-3">⚡ 30초 요약</h2>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {[
            "① KOSPI200 후보군",
            "② 종목별 158개 기술지표 계산",
            "③ AI가 '내일 수익률' 점수화",
            "④ 상위 10개 선별",
            "⑤ 예산·필터 통과분 매수 (하루 최대 2종목)",
          ].map((s, i) => (
            <span key={i} className="flex items-center">
              <span className="bg-white border border-blue-300 rounded px-2.5 py-1.5 text-blue-900">{s}</span>
              {i < 4 && <span className="mx-1 text-blue-400">→</span>}
            </span>
          ))}
        </div>
        <p className="text-xs text-blue-800/70 mt-3">
          매일 장 마감 후(15:45) 데이터를 갱신하고 모델을 다시 학습해 다음 거래일 매수 목록을 만들고,
          다음 날 09:00 개장과 동시에 자동 주문합니다.
        </p>
      </section>

      <Step n="1" title="후보군 — KOSPI200">
        <p className="text-gray-700 text-sm mb-2">
          매수 후보는 <strong>KOSPI200 구성종목(~191개)</strong>으로 한정합니다.
          대형주 위주라 유동성이 풍부해 시장가 주문의 체결 안정성이 높고, 데이터 품질도 좋습니다.
        </p>
        <p className="text-xs text-gray-500">코스닥·소형주는 데이터만 수집하며 매매 대상이 아닙니다.</p>
      </Step>

      <Step n="2" title="재료 — 기술지표 158개 (Alpha158)">
        <p className="text-gray-700 text-sm mb-3">
          각 종목의 <strong>가격·거래량 흐름만으로</strong> 계산하는 158개 지표가 판단 재료입니다.
          재무제표·뉴스·테마는 일절 반영하지 않습니다. 대시보드의 &ldquo;모델 기여 지표&rdquo;에 나오는
          이름들이 바로 이것입니다.
        </p>
        <div className="overflow-x-auto">
        <table className="min-w-full border border-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left font-semibold border-b">카테고리</th>
              <th className="px-3 py-2 text-left font-semibold border-b">대표 지표</th>
              <th className="px-3 py-2 text-left font-semibold border-b">무엇을 보나</th>
            </tr>
          </thead>
          <tbody className="text-gray-700">
            <tr><td className="px-3 py-1.5 border-b">모멘텀</td><td className="px-3 py-1.5 border-b font-mono text-xs">ROC5~60</td><td className="px-3 py-1.5 border-b">5~60일 수익률 — 최근 얼마나 올랐/내렸나</td></tr>
            <tr><td className="px-3 py-1.5 border-b">추세</td><td className="px-3 py-1.5 border-b font-mono text-xs">BETA · RSQR · RESI</td><td className="px-3 py-1.5 border-b">추세의 기울기·신뢰도·이탈 정도</td></tr>
            <tr><td className="px-3 py-1.5 border-b">변동성</td><td className="px-3 py-1.5 border-b font-mono text-xs">STD · WVMA</td><td className="px-3 py-1.5 border-b">가격 출렁임, 거래량 가중 변동성</td></tr>
            <tr><td className="px-3 py-1.5 border-b">가격 위치</td><td className="px-3 py-1.5 border-b font-mono text-xs">MAX · MIN · RSV · QTLU</td><td className="px-3 py-1.5 border-b">기간 고점/저점/분위 대비 현재 위치</td></tr>
            <tr><td className="px-3 py-1.5 border-b">캔들 형태</td><td className="px-3 py-1.5 border-b font-mono text-xs">KMID · KLEN · KUP</td><td className="px-3 py-1.5 border-b">몸통/꼬리 비율 — 당일 매수·매도 세력</td></tr>
            <tr><td className="px-3 py-1.5">거래량</td><td className="px-3 py-1.5 font-mono text-xs">VMA · CORR · VSUMP</td><td className="px-3 py-1.5">거래량 급증, 가격-거래량 동행 여부</td></tr>
          </tbody>
        </table>
        </div>
      </Step>

      <Step n="3" title="판단 — AI 모델의 점수화">
        <ul className="list-disc pl-6 text-gray-700 text-sm space-y-1.5">
          <li><strong>LightGBM</strong>(그래디언트 부스팅 트리)이 매일 저녁 <strong>다시 학습</strong>합니다 — 학습: 2023년 4월~약 3개월 전, 검증: 최근 3개월 (walk-forward, 매일 하루씩 전진)</li>
          <li>예측 목표는 <strong>&ldquo;내일 하루 수익률&rdquo;</strong> — 종목 간 상대 비교(z-score)로 정규화</li>
          <li>결과물: 종목별 <strong>알파 점수</strong> → 내림차순 순위가 곧 매수 우선순위</li>
        </ul>
        <div className="bg-amber-50 border border-amber-200 rounded p-3 mt-3 text-xs text-amber-900">
          <strong>점수 읽는 법</strong>: 점수 차이가 클수록 모델의 확신이 강합니다. 하위권에서 점수가
          동률로 나오면 그 구간은 모델이 변별하지 못한 것입니다(순위는 편의상 코드순).
          상위 1~2위의 점수 크기를 보는 게 중요합니다.
        </div>
      </Step>

      <Step n="4" title="매수 결정 규칙 (핵심)">
        <ol className="list-none space-y-2.5 text-sm text-gray-700">
          {[
            ["목표 포트폴리오", "점수 상위 10개(top-10)를 각 10% 균등비중으로 보유하는 것이 목표 상태"],
            ["하루 최대 2종목", "미보유 종목 중 순위 높은 것부터 하루 최대 2종목만 신규 매수 — 신호가 하루 흔들려도 포트폴리오가 급전환되지 않도록"],
            ["종목당 예산 = 총자산 ÷ 10", "“슬롯 예산” — 한 종목이 가져야 할 목표 금액. 계좌 1,000만원이면 종목당 약 100만원"],
            ["고가주 필터", "1주 가격이 슬롯 예산을 넘는 종목(예: 100만원 초과)은 목표 비중을 만들 수 없으므로 제외하고 다음 순위가 자리를 이어받음"],
            ["수량 = 슬롯 예산 ÷ 전일 종가", "내림(소수점 절사). 저가주는 수백 주, 고가주는 몇 주"],
            ["09:00 시장가 주문", "개장 동시호가 참여. 매도 먼저(현금 확보) → 매수. 주문 간 1.2초 간격(KIS 규정)"],
          ].map(([t, d], i) => (
            <li key={i} className="flex gap-2">
              <span className="shrink-0 w-6 h-6 rounded-full bg-gray-800 text-white text-xs flex items-center justify-center">{i + 1}</span>
              <span><strong>{t}</strong> — {d}</span>
            </li>
          ))}
        </ol>
      </Step>

      {/* 실전 예시 */}
      <section className="bg-emerald-50 border border-emerald-200 rounded-lg p-5">
        <h2 className="text-xl font-bold text-emerald-900 mb-3">🧾 실전 예시 — 2026-07-29 실제 첫 매수</h2>
        <p className="text-sm text-emerald-900/80 mb-3">
          계좌 1,000만원 → 슬롯 예산 = 10,000,000 ÷ 10 = <strong>1,000,000원</strong>
        </p>
        <div className="overflow-x-auto">
        <table className="min-w-full border border-emerald-200 bg-white text-sm">
          <thead className="bg-emerald-100/60">
            <tr>
              <th className="px-3 py-2 text-left font-semibold border-b">신호 순위</th>
              <th className="px-3 py-2 text-left font-semibold border-b">종목</th>
              <th className="px-3 py-2 text-left font-semibold border-b">필터 판정</th>
              <th className="px-3 py-2 text-left font-semibold border-b">결과</th>
            </tr>
          </thead>
          <tbody className="text-gray-700">
            <tr>
              <td className="px-3 py-2 border-b">1위</td>
              <td className="px-3 py-2 border-b">셀바스AI (9,020원/주)</td>
              <td className="px-3 py-2 border-b text-emerald-700">통과 (9,020 ≤ 100만)</td>
              <td className="px-3 py-2 border-b font-mono">1,000,000÷9,020 = <strong>110주</strong> 매수 (~99만)</td>
            </tr>
            <tr>
              <td className="px-3 py-2 border-b">2위</td>
              <td className="px-3 py-2 border-b">삼성바이오로직스 (1,549,000원/주)</td>
              <td className="px-3 py-2 border-b text-red-600">제외 — 1주 가격 155만 &gt; 슬롯 100만</td>
              <td className="px-3 py-2 border-b">다음 순위로 슬롯 이월</td>
            </tr>
            <tr>
              <td className="px-3 py-2">3위</td>
              <td className="px-3 py-2">기아 (125,400원/주)</td>
              <td className="px-3 py-2 text-emerald-700">통과 (대체 진입)</td>
              <td className="px-3 py-2 font-mono">1,000,000÷125,400 = <strong>8주</strong> 매수 (~100만)</td>
            </tr>
          </tbody>
        </table>
        </div>
        <p className="text-xs text-emerald-900/70 mt-2">
          → 첫날 2종목 · 약 200만원 투입. 이후 매일 최대 2종목씩 추가돼 약 5거래일에 걸쳐
          10종목 완전투자 상태로 수렴합니다.
        </p>
      </section>

      <Step n="5" title="매도 기준">
        <ul className="list-disc pl-6 text-gray-700 text-sm space-y-1.5">
          <li>매도 트리거는 단 하나 — <strong>보유 종목이 그날 신호 top-10에서 빠졌을 때</strong> (하루 최대 2종목, 전량 시장가)</li>
          <li>이탈 매도의 세 가지 성격: ① 급등 후 모멘텀 소진(익절성) ② 다른 종목이 상대적으로 좋아져 밀림(교체) ③ 하락+전망 악화(손절성)</li>
          <li><strong>손절매·익절 라인·현금비중 관리는 없습니다</strong> — 급락해도 top-10에 남아 있으면 계속 보유. 리스크 관리는 10종목 분산과 매일 재평가에 의존</li>
          <li className="text-gray-500">예외 — <strong>시뮬 전략 5종(close·flow·trail·scale·limit)</strong>은
            신호 이탈 매도 없이 가격 규칙으로만 청산하는 <strong>청산 규칙 실험 매트릭스</strong>
            (2026-08-04 확장): 공통 손절 = 전 저점 이탈(캡 −10%), close/flow 익절 +10%,
            trail은 최고 종가 −7% 트레일링, scale은 사다리 익절(+10% 절반 → +15% 잔여 절반 →
            +20% 전량, 매도 후 플로어 = 직전 단계), limit은 매수부터
            전일 종가 −3% 지정가 예약(랭크순 최대 2개 체결) + 익절 +10% — 상세는 라이브 화면의
            &ldquo;시뮬 전략별 매매 규칙&rdquo; 참고</li>
        </ul>
      </Step>

      {/* 리스크 */}
      <section className="bg-red-50 border border-red-200 rounded-lg p-5">
        <h2 className="text-lg font-bold text-red-900 mb-2">⚠️ 이 전략의 특성과 한계</h2>
        <ul className="list-disc pl-6 text-sm text-red-900/80 space-y-1">
          <li>목표 상태는 <strong>현금 없이 완전투자</strong> — 시장 급락 시 방어 장치 없음</li>
          <li>기술적 지표만 사용 — 실적 발표, 뉴스, 공시는 전혀 반영되지 않음</li>
          <li>하루 단위 리밸런싱 — 장중 급변에는 대응하지 않음</li>
          <li>현재 <strong>모의투자 검증 단계</strong> — 전략 파라미터(종목 수, 리밸런싱 주기, 손절 등)는 실험을 거쳐 조정될 예정</li>
        </ul>
      </section>

      {/* 대시보드 연결 */}
      <section className="border border-gray-200 rounded-lg p-5 bg-white">
        <h2 className="text-lg font-bold text-gray-900 mb-2">🖥️ 대시보드에서 확인하는 법</h2>
        <ul className="list-disc pl-6 text-sm text-gray-700 space-y-1">
          <li><Link href="/live" className="text-blue-600 underline">라이브 대시보드</Link>의 <strong>다음 거래일 추천 종목</strong> — 내일 살 후보와 매수 근거(지표 배지), 행 클릭 시 모델 기여도</li>
          <li><strong>현재 보유 종목</strong> 행 클릭 — 그 종목의 일자별 매매 이력과 각 시점의 판단 기준·평균단가·수익률</li>
          <li><Link href="/guide" className="text-blue-600 underline">매매 가이드</Link> — 백테스트 방법론과 일일 스케줄 전체</li>
        </ul>
      </section>
    </article>
  );
}
