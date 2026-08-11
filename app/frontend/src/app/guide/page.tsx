/**
 * /guide — 운영 중인 시스템 전체를 한 페이지로 정리한 최종 가이드.
 *
 * 라이브 자동매매(실계좌) · 8개 시뮬 곡선 · 카페 역설계 · 급등 전야 · 회고
 * 루프 · 운영 원칙까지, 대시보드가 보여주는 모든 것의 "왜"를 설명한다.
 * 정적 문서 — API 호출 없음. (구 /guide/strategy는 이 페이지로 리다이렉트)
 */

import ChartLink from "@/components/ChartLink";

const CURVE = (c: string) => (
  <span className="inline-block w-3 h-3 rounded-full mr-1.5 align-middle" style={{ backgroundColor: c }} />
);

/** 카페 추천자 실측 표본 이력 — docs/06-research 기록 기준 (est = 손절가 미기재분 프로파일 역산). */
const CAFE_SAMPLES: {
  date: string; name: string; code: string; grammar: string;
  base: number; stop: string; note: string;
}[] = [
  { date: "2026-07-30", name: "뉴엔AI", code: "463020", grammar: "D 낙폭과대 반등", base: 7400, stop: "—", note: "표본 1호" },
  { date: "2026-07-31", name: "씨피시스템", code: "413630", grammar: "C 급등 눌림 재진입", base: 3865, stop: "3,500", note: "2호" },
  { date: "2026-08-03", name: "위닉스", code: "044340", grammar: "A 신고가 돌파 당일", base: 5860, stop: "—", note: "3호" },
  { date: "2026-08-04", name: "씨피시스템", code: "413630", grammar: "눌림 재배팅", base: 4225, stop: "3,800 (래칫↑)", note: "2-b 재추천" },
  { date: "2026-08-04", name: "티엑스알로보틱스", code: "484810", grammar: "B 상한가 후 타이트 눌림", base: 17150, stop: "14,900 (전고점)", note: "4호" },
  { date: "2026-08-04", name: "위닉스", code: "044340", grammar: "한 번 더 파동", base: 5780, stop: "5,000 (돌파일 저가−5%)", note: "3-b 재추천" },
  { date: "2026-08-05", name: "뉴엔AI", code: "463020", grammar: "신고가 돌파 전야", base: 10320, stop: "9,060~9,200 (추정)", note: "1-b 재추천" },
  { date: "2026-08-06", name: "씨피시스템", code: "413630", grammar: "종목명만 (본전 눌림)", base: 4225, stop: "3,800 유지 (추정)", note: "2-c 3차" },
  { date: "2026-08-06", name: "JW신약", code: "067290", grammar: "R 저항대 돌파", base: 2330, stop: "2,020 (돌파 기점)", note: "5호" },
  { date: "2026-08-07", name: "이랜시스", code: "264850", grammar: "눌림 저점 버퍼", base: 6470, stop: "5,850", note: "6호 ★스크리너 1일 선행" },
  { date: "2026-08-07", name: "솔트룩스", code: "304100", grammar: "5일선 눌림목", base: 17090, stop: "14,900", note: "7호" },
  { date: "2026-08-10", name: "JW신약", code: "067290", grammar: "본전 재배팅", base: 2340, stop: "2,000 (라운드화)", note: "5-b 재추천" },
  { date: "2026-08-10", name: "티엑스알로보틱스", code: "484810", grammar: "본전 이하 첫 재배팅", base: 16330, stop: "15,340 (당일 저점)", note: "4-b 재추천" },
  { date: "2026-08-10", name: "지엔씨에너지", code: "119850", grammar: "C 눌림 흡수 종가 베팅", base: 52800, stop: "48,150 (추정)", note: "8호" },
  { date: "2026-08-11", name: "신스틸", code: "162300", grammar: "상한가 소화 후 재돌파 (첫 장중 추천)", base: 2195, stop: "1,930", note: "9호" },
];

export default function GuidePage() {
  return (
    <article className="prose-sm md:prose max-w-none space-y-10">
      <header className="border-b border-gray-200 pb-6">
        <h1 className="text-3xl md:text-4xl font-bold text-gray-900">📖 시스템 가이드 — 운영 전략 최종 정리</h1>
        <p className="mt-3 text-gray-600">
          이 시스템은 <strong>KIS 모의계좌 3개월 테스트</strong>로 9개 전략을 병렬 검증한 뒤,
          승자를 <strong>토스증권 실전 자동매매</strong>로 전환하는 실험입니다. 실계좌 1개(open)와
          시뮬 8개가 같은 시장에서 매일 경쟁하고, 모든 판단 근거·체결·회고가 기록됩니다.
        </p>
      </header>

      {/* 1. 하루 타임라인 */}
      <section id="timeline" className="not-prose bg-gray-50 border border-gray-200 rounded-lg p-5">
        <h2 className="text-2xl font-bold text-gray-900 mb-3">📅 하루 전체 타임라인 (KST · 평일)</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full bg-white border border-gray-200 text-sm">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-3 py-2 text-left border-b">시각</th>
                <th className="px-3 py-2 text-left border-b">동작</th>
                <th className="px-3 py-2 text-left border-b">텔레그램</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              <tr><td className="px-3 py-1.5 font-mono">09:00</td><td className="px-3 py-1.5"><strong>실계좌(open) 주문</strong> — 매도(순위 이탈) 먼저, 매수(신규 top-10) 시장가</td><td className="px-3 py-1.5">🌅 주문 상세</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">09:20</td><td className="px-3 py-1.5">체결가 대사 — 실체결가·실현손익 확정 저장</td><td className="px-3 py-1.5">✅ 체결·손익</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">09:30</td><td className="px-3 py-1.5">실계좌 잔고 동기화</td><td className="px-3 py-1.5">—</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">14:30 / 15:00</td><td className="px-3 py-1.5">🔭 <strong>정찰 스캔</strong> — 카페 규칙 후보 미리보기 (매매 없음, 조기 진입 연구)</td><td className="px-3 py-1.5">🔭 후보 5</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:05</td><td className="px-3 py-1.5">☕ <strong>카페 정규 스크린</strong> — 패턴 후보 확정 + 풀 전체 전야 피처 저장</td><td className="px-3 py-1.5">☕ 후보·손절</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:12</td><td className="px-3 py-1.5">⚡ <strong>급등 전야 TOP10</strong> 선정 (풀 스냅샷 채점 — KIS 추가 호출 0)</td><td className="px-3 py-1.5">⚡ TOP10</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:20~26</td><td className="px-3 py-1.5">close·flow·trail·scale 시뮬 매수 (신호 픽, 실시간가)</td><td className="px-3 py-1.5">—</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:28 / 15:29</td><td className="px-3 py-1.5">cafe / surge 시뮬 매수 (각자 후보 상위 2, 실시간가)</td><td className="px-3 py-1.5">—</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:40~47</td><td className="px-3 py-1.5">전략별 계좌 동기화 · 일일 손익 확정</td><td className="px-3 py-1.5">—</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:45</td><td className="px-3 py-1.5">주가 데이터 갱신 → <strong>모델 재학습·다음 거래일 신호 생성</strong></td><td className="px-3 py-1.5">📌 추천 TOP10</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:48</td><td className="px-3 py-1.5"><strong>브래킷 청산 판정</strong>(전 시뮬, 당일 봉 기준) + limit 예약 체결 판정</td><td className="px-3 py-1.5">📤 청산 (발생 시)</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">16:20 / 16:25</td><td className="px-3 py-1.5">신호·판정 폴백 재실행 (멱등)</td><td className="px-3 py-1.5">—</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">18:10</td><td className="px-3 py-1.5">기관·외국인 수급 데이터 적재 (flow 전략용)</td><td className="px-3 py-1.5">—</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      {/* 2. 전략 매트릭스 */}
      <section id="strategies" className="not-prose">
        <h2 className="text-2xl font-bold text-gray-900 mb-1">🎯 9전략 매트릭스 — 무엇을 검증하는가</h2>
        <p className="text-sm text-gray-600 mb-3">
          모든 전략의 시드는 1,000만 원. 곡선 간 격차가 곧 실험의 답입니다.
          <strong className="text-amber-700"> 공정 비교 구간은 2026-08-06 이후</strong>
          (그 이전 시뮬은 전일 종가 체결 편향으로 무효 — 8/6 클린 리셋).
        </p>
        <div className="overflow-x-auto">
          <table className="min-w-full bg-white border border-gray-200 text-sm">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-3 py-2 text-left border-b">전략</th>
                <th className="px-3 py-2 text-left border-b">종목 소스</th>
                <th className="px-3 py-2 text-left border-b">진입</th>
                <th className="px-3 py-2 text-left border-b">청산</th>
                <th className="px-3 py-2 text-left border-b">검증 질문</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 align-top">
              <tr>
                <td className="px-3 py-2 whitespace-nowrap">{CURVE("#10b981")}<strong>open</strong> (실계좌)</td>
                <td className="px-3 py-2">qlib 신호 top-10</td>
                <td className="px-3 py-2">09:00 시장가 (하루 최대 2)</td>
                <td className="px-3 py-2">top-30 이탈 시 매도 (n_drop=2)</td>
                <td className="px-3 py-2">기준선 — 모델 그대로의 성적</td>
              </tr>
              <tr>
                <td className="px-3 py-2 whitespace-nowrap">{CURVE("#6366f1")}<strong>close</strong></td>
                <td className="px-3 py-2">〃</td>
                <td className="px-3 py-2">15:20 실시간가</td>
                <td className="px-3 py-2">익절 +10% / 전 저점 손절 (캡 −10%)</td>
                <td className="px-3 py-2">종가 진입 + 가격 브래킷이 순위 이탈보다 나은가</td>
              </tr>
              <tr>
                <td className="px-3 py-2 whitespace-nowrap">{CURVE("#f59e0b")}<strong>flow</strong></td>
                <td className="px-3 py-2">top-30을 기관·외인 순매수로 재랭킹</td>
                <td className="px-3 py-2">15:22 실시간가</td>
                <td className="px-3 py-2">close와 동일</td>
                <td className="px-3 py-2">수급 오버레이의 부가가치</td>
              </tr>
              <tr>
                <td className="px-3 py-2 whitespace-nowrap">{CURVE("#0ea5e9")}<strong>trail</strong></td>
                <td className="px-3 py-2">qlib 신호</td>
                <td className="px-3 py-2">15:24 실시간가</td>
                <td className="px-3 py-2">익절 없음 — 최고 종가 −7% 트레일링</td>
                <td className="px-3 py-2">&ldquo;일찍 팔지 않기&rdquo;의 가치 (조기하차 가설 해답 후보)</td>
              </tr>
              <tr>
                <td className="px-3 py-2 whitespace-nowrap">{CURVE("#a855f7")}<strong>scale</strong></td>
                <td className="px-3 py-2">〃</td>
                <td className="px-3 py-2">15:26 실시간가</td>
                <td className="px-3 py-2">사다리: +10% 절반 → +15% 잔여 절반 → +20% 전량, 래칫 플로어</td>
                <td className="px-3 py-2">분할 익절의 균형점</td>
              </tr>
              <tr>
                <td className="px-3 py-2 whitespace-nowrap">{CURVE("#ef4444")}<strong>limit</strong></td>
                <td className="px-3 py-2">〃</td>
                <td className="px-3 py-2"><strong>전일 종가 −3% 지정가 예약</strong> (5후보, 최대 2체결, 미체결 취소)</td>
                <td className="px-3 py-2">익절 +10% / 전 저점 손절</td>
                <td className="px-3 py-2">★ 실전 확정 주문 정책 — 지정가의 체결률·가격 개선 실측</td>
              </tr>
              <tr>
                <td className="px-3 py-2 whitespace-nowrap">{CURVE("#78716c")}<strong>cafe</strong></td>
                <td className="px-3 py-2">카페 추천자 역설계 스크리너 (전 시장)</td>
                <td className="px-3 py-2">15:28 실시간가 (상위 2)</td>
                <td className="px-3 py-2">익절 +10% / <strong>구조적 손절</strong>(패턴 앵커, 캡 −15%)</td>
                <td className="px-3 py-2">추천자의 문법이 규칙으로도 통하는가</td>
              </tr>
              <tr>
                <td className="px-3 py-2 whitespace-nowrap">{CURVE("#db2777")}<strong>surge</strong></td>
                <td className="px-3 py-2">급등 전야 프로파일 TOP10 (전 시장)</td>
                <td className="px-3 py-2">15:29 실시간가 (상위 2)</td>
                <td className="px-3 py-2">close와 동일 (진입 아이디어만 분리 검증)</td>
                <td className="px-3 py-2">통계 프로파일이 &ldquo;내일의 급등&rdquo;을 맞추는가</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      {/* 3. qlib 신호 */}
      <section id="signal" className="not-prose bg-emerald-50/50 border border-emerald-200 rounded-lg p-5">
        <h2 className="text-2xl font-bold text-emerald-900 mb-2">🤖 qlib 신호 — 모델이 종목을 고르는 법</h2>
        <ul className="list-disc pl-6 text-sm text-gray-700 space-y-1.5">
          <li><strong>유니버스</strong>: 코스피200 + 코스닥150 (~185종목 실채점) · 3년 학습 데이터</li>
          <li><strong>모델</strong>: Alpha158 피처 → LightGBM, <strong>고정 150라운드 학습</strong> —
            조기종료 방식은 검증 신호가 소음 수준이라 운에 따라 트리 1개짜리 모델이 나오는 결함
            (점수 동점 사태의 원인)이 있어 2026-08-05 진단 후 제거</li>
          <li><strong>종합점수 = 전체 대비 백분위</strong> (&ldquo;상위 0.5%&rdquo;) — 당일 채점된 전 종목 중 위치.
            픽 내 상대점수(0~100)는 구신호 폴백 표기</li>
          <li><strong>안전망</strong>: 거래정지·관리종목·투자위험/경고 종목은 순위와 무관하게 주문 직전
            스킵(콘텐트리중앙 사례) · 거래 동결 종목 신호 제외 · 슬롯 예산 초과 고가주 제외</li>
          <li><strong>매도 사유·매수 근거</strong>는 주문마다 스냅샷 저장 — 회고의 원재료</li>
        </ul>
      </section>

      {/* 4. 카페 역설계 */}
      <section id="cafe" className="not-prose bg-stone-50 border border-stone-200 rounded-lg p-5">
        <h2 className="text-2xl font-bold text-stone-800 mb-2">☕ 카페 역설계 — 추천자의 시스템을 규칙으로</h2>
        <p className="text-sm text-gray-700 mb-3">
          실존 추천자의 표본 <strong>9종목 15표본(재추천 6건 포함)</strong>을 역설계한 결과:
          <strong>&ldquo;주도 테마 + 기술적 이벤트 날 종가 진입 + 구조적 손절 + 래칫 +
          동일 종목 재배팅&rdquo;</strong>. 이를 5개 패턴으로 코딩해 매일 전 시장을 스캔합니다.
          아래 표본 이력의 차트↗로 당시 봉을 직접 검증할 수 있습니다.
        </p>
        <div className="overflow-x-auto mb-3">
          <table className="min-w-full bg-white border border-stone-200 text-sm">
            <thead className="bg-stone-100">
              <tr><th className="px-3 py-2 text-left border-b">패턴</th><th className="px-3 py-2 text-left border-b">조건 요약</th><th className="px-3 py-2 text-left border-b">손절 앵커</th></tr>
            </thead>
            <tbody className="divide-y divide-stone-100">
              <tr><td className="px-3 py-1.5"><strong>B</strong> 급등 타이트 눌림</td><td className="px-3 py-1.5">전일 +15%↑ 후 오늘 진폭 ≤6%</td><td className="px-3 py-1.5">급등 전 스윙 고점</td></tr>
              <tr><td className="px-3 py-1.5"><strong>A</strong> 신고가 돌파</td><td className="px-3 py-1.5">30일 종가 신고 + 20일 +30% + 거래량 2×</td><td className="px-3 py-1.5">깨진 종가 고점 ×0.97</td></tr>
              <tr><td className="px-3 py-1.5"><strong>R</strong> 저항대 돌파</td><td className="px-3 py-1.5">5일 저항 돌파(신고가 미달) + 20일 +15% + 거래량 2×</td><td className="px-3 py-1.5">돌파한 저항 레벨 ×0.97 (JW신약 검증 1% 오차)</td></tr>
              <tr><td className="px-3 py-1.5"><strong>C</strong> 급등 눌림 재진입</td><td className="px-3 py-1.5">+25% 급등 후 −8~25% 눌림 + 양봉 반전</td><td className="px-3 py-1.5">눌림 저점 ×0.96</td></tr>
              <tr><td className="px-3 py-1.5"><strong>D</strong> 낙폭과대 반등</td><td className="px-3 py-1.5">고점 −40% + 당일 +5% + 20일선 회복</td><td className="px-3 py-1.5">당일 저가 ×0.97</td></tr>
            </tbody>
          </table>
        </div>
        <details className="mb-3 bg-white border border-stone-200 rounded" open>
          <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-stone-800 bg-stone-100">
            📜 실제 추천 표본 이력 (역설계 원천 데이터 — 종목·추천일·손절 앵커)
          </summary>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm whitespace-nowrap">
              <thead className="bg-stone-50 text-stone-600">
                <tr>
                  <th className="px-3 py-2 text-left border-b">추천일</th>
                  <th className="px-3 py-2 text-left border-b">종목 (코드)</th>
                  <th className="px-3 py-2 text-left border-b">문법/유형</th>
                  <th className="px-3 py-2 text-right border-b">기준가</th>
                  <th className="px-3 py-2 text-left border-b">손절 (앵커)</th>
                  <th className="px-3 py-2 text-left border-b">표본</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {CAFE_SAMPLES.map((s, i) => (
                  <tr key={`${s.date}-${s.code}-${i}`}>
                    <td className="px-3 py-1.5 font-mono text-xs text-stone-500">{s.date}</td>
                    <td className="px-3 py-1.5">
                      <span className="text-gray-900">{s.name}</span>
                      <span className="font-mono text-xs text-gray-400 ml-1.5">({s.code})</span>
                      <ChartLink code={s.code} className="ml-1.5" />
                    </td>
                    <td className="px-3 py-1.5">{s.grammar}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{s.base.toLocaleString()}</td>
                    <td className="px-3 py-1.5">{s.stop}</td>
                    <td className="px-3 py-1.5 text-xs text-stone-600">{s.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="px-3 py-2 text-xs text-stone-500 border-t border-stone-100">
            기준가 = 추천일 종가(일부 장중 기록가). &ldquo;추정&rdquo; = 추천자가 2026-08-06부터
            손절가를 미기재 — 확보된 앵커 문법(전저점·전고점·돌파 기점·당일 저점 ±버퍼)으로 역산한 값.
          </p>
        </details>
        <ul className="list-disc pl-6 text-sm text-gray-700 space-y-1">
          <li>표시·저장되는 손절가 = 청산 엔진이 실제로 쓰는 <strong>실효값</strong> (캡 −15% 바닥)</li>
          <li>🔭 정찰(14:30·15:00)은 &ldquo;종가 후보를 더 일찍 알 수 있는가&rdquo;의 측정용 — 매매 없음</li>
          <li>2026-08-07 이정표: <strong>스크리너가 이랜시스를 추천자보다 하루 먼저 선정</strong> —
            게시글 의존 없이 원천 데이터에서 같은 종목이 나온다는 실증</li>
        </ul>
      </section>

      {/* 5. 급등 전야 */}
      <section id="surge" className="not-prose bg-pink-50/60 border border-pink-200 rounded-lg p-5">
        <h2 className="text-2xl font-bold text-pink-900 mb-2">⚡ 급등 전야 프로파일 — 내일 급등주는 오늘 어떤 모습인가</h2>
        <p className="text-sm text-gray-700 mb-2">
          유니버스 3.5년 전수(익일 +8% 급등 2,356건 vs 대조군)에서 추출한 통계 프로파일:
          급등은 바닥이 아니라 <strong>&ldquo;이미 달리는 종목이 5일 고점 대비 −6~7% 눌린 자리&rdquo;</strong>에서
          압도적으로 많이 나옵니다 (추천자의 눌림 문법과 일치).
        </p>
        <ul className="list-disc pl-6 text-sm text-gray-700 space-y-1">
          <li>점수 = 20일 추세 + 눌림 적합도(−6.5% 최적) + 거래량 확대 + 20일선 위 —
            추세 +5% 미만·눌림 아님(−1%~−15% 밖)은 탈락</li>
          <li>매일 15:12 TOP10 발표(15:05이 저장한 풀 스냅샷 재사용) → 상위 2 시뮬 매수</li>
          <li>회고의 <strong>선정력 채점표</strong>가 매수 여부와 무관하게 TOP10 전체의 익일 적중을 기록</li>
        </ul>
      </section>

      {/* 6. 회고 루프 */}
      <section id="retro" className="not-prose bg-white border border-gray-200 rounded-lg p-5">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">🔁 회고 루프 — 시스템이 스스로를 채점하는 법</h2>
        <ul className="list-disc pl-6 text-sm text-gray-700 space-y-1.5">
          <li><a href="/live/retro" className="text-blue-600 underline">/live/retro</a>: 전략별 에피소드 원장
            (진입 근거 × 실현% × give-back × <strong>매도 후 5일 드리프트</strong>), 가설 스코어보드,
            신호 rank IC, surge 선정력</li>
          <li>추적 중인 가설: 과열 진입 저성과 · 순위 이탈 매도의 조기하차 · 갭 추격 · 재개주 위험</li>
          <li><strong>반영 게이트</strong>: 가설 채점 → 임계 도달 → 사용자 승인 → 시뮬 곡선 검증 →
            반영. 즉흥 수정 금지</li>
          <li>매주 금요일 정기 회고 + 매일 IC 기록 (일일 점검 에이전트)</li>
        </ul>
      </section>

      {/* 7. 운영 원칙 */}
      <section id="principles" className="not-prose bg-blue-50/50 border border-blue-200 rounded-lg p-5">
        <h2 className="text-2xl font-bold text-blue-900 mb-2">🧊 운영 원칙과 이력</h2>
        <ul className="list-disc pl-6 text-sm text-gray-700 space-y-1.5">
          <li><strong>전략 동결</strong>: 테스트 종료까지 설계 변경 금지 — 원인 귀속 보호.
            버그 수정(설계와 다르게 동작)만 승인 후 허용, 관측·연구 기능은 자유</li>
          <li><strong>데이터 이력</strong>: 2026-08-05 이전 시뮬 성적은 전일 종가 체결 편향으로 무효 →
            8/6 클린 리셋 후 실시간가 공정 체결. 시뮬 체결가에는 quote/폴백 출처 기록</li>
          <li><strong>실전 전환 주문 정책 (확정)</strong>: 매수는 시장가 금지 — 분석 기준가
            <strong> −3% 지정가 예약</strong> (limit 곡선이 이 정책의 성과를 실측 중)</li>
          <li><strong>소수점 평단?</strong> 한국 주식은 정수 호가로만 체결 — 28,120.27 같은 평단은
            시장가 주문이 여러 호가에 분할 체결된 <strong>가중평균</strong>입니다</li>
          <li><strong>장세 각주</strong>: 2026년은 급등 빈도가 예년의 2~3배인 정책 강세장 —
            최종 판정은 절대 수익이 아니라 <strong>벤치마크 대비 초과수익</strong>으로</li>
        </ul>
      </section>

      {/* 8. 알림 요약 */}
      <section id="alerts" className="not-prose">
        <h2 className="text-2xl font-bold text-gray-900 mb-3">🔔 텔레그램 알림 한눈에</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full bg-white border border-gray-200 text-sm">
            <thead className="bg-gray-100">
              <tr><th className="px-3 py-2 text-left border-b">시각</th><th className="px-3 py-2 text-left border-b">알림</th><th className="px-3 py-2 text-left border-b">내용</th></tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              <tr><td className="px-3 py-1.5 font-mono">09:00</td><td className="px-3 py-1.5">🌅 아침 주문</td><td className="px-3 py-1.5">종목별 매수/매도·수량·상태·사유 (차트 링크)</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">09:20</td><td className="px-3 py-1.5">✅ 체결 확정</td><td className="px-3 py-1.5">실체결가·총액·매도 실현손익</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">14:30·15:00</td><td className="px-3 py-1.5">🔭 정찰</td><td className="px-3 py-1.5">카페 규칙 후보 미리보기 (관측 전용)</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:05</td><td className="px-3 py-1.5">☕ 정규 스크린</td><td className="px-3 py-1.5">확정 후보 + 실효 손절가 + 매수 예고</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:12</td><td className="px-3 py-1.5">⚡ 급등 TOP10</td><td className="px-3 py-1.5">전야 프로파일 점수순 10종목</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">~15:50</td><td className="px-3 py-1.5">📌 추천 TOP10</td><td className="px-3 py-1.5">qlib 내일 픽 — 종목·종가·상위%</td></tr>
              <tr><td className="px-3 py-1.5 font-mono">15:48</td><td className="px-3 py-1.5">📤 시뮬 청산</td><td className="px-3 py-1.5">익절/손절 발생 시 — 종류·수량·손익·손절 기준</td></tr>
            </tbody>
          </table>
        </div>
        <p className="text-xs text-gray-400 mt-3">
          모든 알림의 종목명은 트레이딩뷰 차트 링크입니다. 대시보드: <a href="/live" className="text-blue-600 underline">/live</a> ·
          회고: <a href="/live/retro" className="text-blue-600 underline">/live/retro</a> ·
          주문 이력: <a href="/live/orders" className="text-blue-600 underline">/live/orders</a>
        </p>
      </section>
    </article>
  );
}
