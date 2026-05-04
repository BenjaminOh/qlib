/**
 * /guide — Trading methodology overview.
 *
 * Static documentation page describing how the system trades:
 * universe → Alpha158 features → ML model → daily signal → topk-dropout
 * portfolio → KIS order execution → balance sync. Read-only, no API calls.
 */

export default function GuidePage() {
  return (
    <article className="prose-sm md:prose lg:prose-lg max-w-none space-y-12">
      <header className="border-b border-gray-200 pb-6">
        <h1 className="text-3xl md:text-4xl font-bold text-gray-900">
          📖 매매 가이드
        </h1>
        <p className="mt-3 text-gray-600">
          이 시스템이 어떻게 종목을 선택하고, 언제 사고팔며, 어떤 지표로 평가하는지 단계별로 설명합니다.
        </p>
      </header>

      {/* 1. 유니버스 */}
      <section>
        <h2 className="text-2xl font-bold text-gray-900 mb-4">
          1. 종목 유니버스 (Universe)
        </h2>
        <p className="text-gray-700 mb-4">
          매매 대상이 되는 종목 풀입니다. 이 풀 안에서만 모델이 점수를 매기고 거래가 일어납니다.
        </p>
        <table className="min-w-full border border-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700 border-b">시장</th>
              <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700 border-b">종목 수</th>
              <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700 border-b">용도</th>
            </tr>
          </thead>
          <tbody>
            <tr><td className="px-4 py-2 border-b">KOSPI 200</td><td className="px-4 py-2 border-b">200</td><td className="px-4 py-2 border-b">대형주</td></tr>
            <tr><td className="px-4 py-2 border-b">KOSDAQ 150</td><td className="px-4 py-2 border-b">150</td><td className="px-4 py-2 border-b">중소형 성장주</td></tr>
            <tr><td className="px-4 py-2 border-b">KODEX 200 ETF (069500)</td><td className="px-4 py-2 border-b">1</td><td className="px-4 py-2 border-b">벤치마크 (KOSPI 추종)</td></tr>
            <tr className="bg-blue-50 font-semibold"><td className="px-4 py-2">합계</td><td className="px-4 py-2">~351</td><td className="px-4 py-2">매매 후보 풀</td></tr>
          </tbody>
        </table>
      </section>

      {/* 2. Alpha158 */}
      <section>
        <h2 className="text-2xl font-bold text-gray-900 mb-4">
          2. 특성 추출 — Alpha158
        </h2>
        <p className="text-gray-700 mb-4">
          qlib의 <code className="bg-gray-100 px-1.5 py-0.5 rounded text-sm">Alpha158</code> 핸들러가 매 영업일 × 매 종목별로 <strong>158개 기술적 지표</strong>를 계산합니다. 이 지표들이 ML 모델의 입력값이 됩니다.
        </p>
        <ul className="list-disc pl-6 text-gray-700 space-y-1">
          <li><strong>가격 모멘텀</strong> — 5일/10일/20일/60일 수익률</li>
          <li><strong>변동성</strong> — rolling standard deviation</li>
          <li><strong>거래량 패턴</strong> — 거래량 변화율, 거래대금 비율</li>
          <li><strong>고전 지표</strong> — MACD, RSI, 볼린저 밴드 위치</li>
          <li><strong>Cross-sectional rank</strong> — 같은 날 다른 종목 대비 순위</li>
        </ul>
        <p className="text-gray-600 mt-4 text-sm">
          ※ 더 많은 지표(360개)를 쓰는 <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">Alpha360</code>도 선택 가능. 백테스트 폼에서 핸들러 변경.
        </p>
      </section>

      {/* 3. ML 모델 */}
      <section>
        <h2 className="text-2xl font-bold text-gray-900 mb-4">
          3. ML 모델 — 다음 날 수익률 예측
        </h2>
        <p className="text-gray-700 mb-4">
          158개 특성을 입력으로 받아 <strong>다음 영업일 수익률</strong>을 예측하는 회귀(regression) 모델입니다. 백테스트 시 4가지 중 선택:
        </p>
        <table className="min-w-full border border-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700 border-b">모델</th>
              <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700 border-b">특징</th>
              <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700 border-b">권장</th>
            </tr>
          </thead>
          <tbody className="text-sm">
            <tr><td className="px-4 py-2 border-b font-mono">LGBModel</td><td className="px-4 py-2 border-b">LightGBM 기반. 빠르고 일반적으로 최고 성능</td><td className="px-4 py-2 border-b text-green-600 font-semibold">기본</td></tr>
            <tr><td className="px-4 py-2 border-b font-mono">XGBModel</td><td className="px-4 py-2 border-b">XGBoost 기반. LightGBM과 유사, 메모리 더 사용</td><td className="px-4 py-2 border-b">대안</td></tr>
            <tr><td className="px-4 py-2 border-b font-mono">CatBoostModel</td><td className="px-4 py-2 border-b">범주형 처리 강함. 한국 시장엔 큰 차이 없음</td><td className="px-4 py-2 border-b">대안</td></tr>
            <tr><td className="px-4 py-2 border-b font-mono">LinearModel</td><td className="px-4 py-2 border-b">선형 회귀. 단순/빠름. 베이스라인 비교용</td><td className="px-4 py-2 border-b">베이스라인</td></tr>
          </tbody>
        </table>
        <p className="text-gray-700 mt-4">
          <strong>학습 흐름</strong>:
        </p>
        <pre className="bg-gray-100 text-gray-800 p-4 rounded text-sm overflow-x-auto mt-2">{`train: 2023-01-01 ~ 2024-06-30   (모델 학습)
valid: 2024-07-01 ~ 2024-09-30   (하이퍼파라미터 튜닝)
test:  2024-10-01 ~ 오늘          (out-of-sample 검증)`}</pre>
      </section>

      {/* 4. 일일 신호 */}
      <section>
        <h2 className="text-2xl font-bold text-gray-900 mb-4">
          4. 일일 신호 — 매일 15:35 KST (장 마감 후 5분)
        </h2>
        <p className="text-gray-700 mb-4">
          Celery Beat가 <code className="bg-gray-100 px-1.5 py-0.5 rounded text-sm">live_signal</code> 작업을 트리거합니다.
        </p>
        <ol className="list-decimal pl-6 text-gray-700 space-y-2">
          <li>오늘까지 데이터로 모델 점수 갱신</li>
          <li>~351 종목 전체에 대해 <strong>내일 예상 수익률 점수</strong> 계산</li>
          <li>점수 상위 K개 (기본 30) 추출</li>
          <li>DB의 <code className="bg-gray-100 px-1.5 py-0.5 rounded text-sm">Signal</code> 테이블에 저장</li>
        </ol>
      </section>

      {/* 5. 포트폴리오 전략 */}
      <section>
        <h2 className="text-2xl font-bold text-gray-900 mb-4">
          5. 포트폴리오 전략 — TopkDropoutStrategy
        </h2>
        <p className="text-gray-700 mb-4">
          상위 K개를 보유하고, 매일 점수 최하위 N개를 <strong>탈락(drop)</strong>시키고 새 상위 N개를 <strong>편입</strong>합니다.
        </p>
        <h3 className="text-lg font-semibold text-gray-800 mt-6 mb-2">동작 예시 (K=6, n_drop=2)</h3>
        <pre className="bg-gray-100 text-gray-800 p-4 rounded text-sm overflow-x-auto">{`오늘 보유:    A=90  B=85  C=80  D=75  E=70  F=68
신규 후보:    G=92  H=88  I=83  (보유 안 한 상위 종목)

n_drop = 2 (매일 회전 종목 수):
  • 매도: 보유 중 점수 최하위 2개  →  E, F
  • 매수: 신규 후보 상위 2개        →  G, H

내일 보유:  A  B  C  D  G  H`}</pre>
        <h3 className="text-lg font-semibold text-gray-800 mt-6 mb-2">평균 보유 기간 공식</h3>
        <p className="text-gray-700 mb-2">
          <code className="bg-gray-100 px-1.5 py-0.5 rounded">평균 보유일 ≈ K / n_drop</code>
        </p>
        <table className="min-w-full border border-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left border-b">K (보유 종목 수)</th>
              <th className="px-4 py-2 text-left border-b">n_drop (일일 회전)</th>
              <th className="px-4 py-2 text-left border-b">평균 보유일</th>
              <th className="px-4 py-2 text-left border-b">연 회전율</th>
            </tr>
          </thead>
          <tbody>
            <tr><td className="px-4 py-2 border-b">30</td><td className="px-4 py-2 border-b">5</td><td className="px-4 py-2 border-b">6일</td><td className="px-4 py-2 border-b">~4,000%</td></tr>
            <tr><td className="px-4 py-2 border-b">30</td><td className="px-4 py-2 border-b">3</td><td className="px-4 py-2 border-b">10일</td><td className="px-4 py-2 border-b">~2,500%</td></tr>
            <tr><td className="px-4 py-2 border-b">50</td><td className="px-4 py-2 border-b">5</td><td className="px-4 py-2 border-b">10일</td><td className="px-4 py-2 border-b">~2,500%</td></tr>
            <tr><td className="px-4 py-2 border-b">100</td><td className="px-4 py-2 border-b">5</td><td className="px-4 py-2 border-b">20일</td><td className="px-4 py-2 border-b">~1,250%</td></tr>
          </tbody>
        </table>
        <p className="text-gray-600 mt-3 text-sm">
          → <strong>단기 모멘텀/평균회귀 결합형 퀀트 전략</strong>. 장기투자(buy &amp; hold)와 정반대. 빠른 회전으로 모델이 잡은 "내일 오를 종목" 기회를 노림.
        </p>

        <h3 className="text-lg font-semibold text-gray-800 mt-6 mb-2">대안 전략</h3>
        <ul className="list-disc pl-6 text-gray-700 space-y-1 text-sm">
          <li><strong>SoftTopkStrategy</strong> — 하드 컷오프 대신 softmax. 점수 비례 비중</li>
          <li><strong>EnhancedIndexingStrategy</strong> — 벤치마크 추적 + 알파 부분 추가</li>
          <li><strong>TWAPStrategy</strong> — 시간 분할 매매로 슬리피지 최소화</li>
          <li><strong>SBBStrategyEMA</strong> — 거래대금 기반 동적 사이즈 조절</li>
        </ul>
      </section>

      {/* 6. 주문 실행 + Sync */}
      <section>
        <h2 className="text-2xl font-bold text-gray-900 mb-4">
          6. 주문 실행 + 동기화 (KIS Open API)
        </h2>
        <table className="min-w-full border border-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left border-b">시각 (KST)</th>
              <th className="px-4 py-2 text-left border-b">작업</th>
              <th className="px-4 py-2 text-left border-b">내용</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="px-4 py-2 border-b font-mono">15:35</td>
              <td className="px-4 py-2 border-b font-mono">live_signal</td>
              <td className="px-4 py-2 border-b">전일 종가 기반 모델 재점수 → 내일 보유 목록 결정</td>
            </tr>
            <tr>
              <td className="px-4 py-2 border-b font-mono">09:00</td>
              <td className="px-4 py-2 border-b font-mono">live_orders</td>
              <td className="px-4 py-2 border-b">현재 잔고 vs 목표 포트폴리오 diff → 시장가 주문 발주</td>
            </tr>
            <tr>
              <td className="px-4 py-2 border-b font-mono">09:30</td>
              <td className="px-4 py-2 border-b font-mono">live_sync</td>
              <td className="px-4 py-2 border-b">주문 체결 확인, Fill 테이블 갱신</td>
            </tr>
            <tr>
              <td className="px-4 py-2 border-b font-mono">15:40</td>
              <td className="px-4 py-2 border-b font-mono">live_sync</td>
              <td className="px-4 py-2 border-b">일일 잔고/PnL 스냅샷, DailyPnL 기록</td>
            </tr>
          </tbody>
        </table>
        <p className="text-gray-600 mt-4 text-sm">
          ※ 평일(월~금)만 실행. 휴장일은 qlib 캘린더 체크로 자동 no-op.
        </p>
      </section>

      {/* 7. 백테스트 지표 */}
      <section>
        <h2 className="text-2xl font-bold text-gray-900 mb-4">
          7. 백테스트 결과 지표 해석
        </h2>
        <table className="min-w-full border border-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left border-b">지표</th>
              <th className="px-4 py-2 text-left border-b">의미</th>
              <th className="px-4 py-2 text-left border-b">좋은 값</th>
            </tr>
          </thead>
          <tbody>
            <tr><td className="px-4 py-2 border-b font-mono">ARR</td><td className="px-4 py-2 border-b">연평균 수익률 (Annual Return Rate)</td><td className="px-4 py-2 border-b">높을수록 ↑ (벤치마크 대비)</td></tr>
            <tr><td className="px-4 py-2 border-b font-mono">IR</td><td className="px-4 py-2 border-b">Information Ratio. 위험조정 알파 (벤치마크 초과수익/추적오차)</td><td className="px-4 py-2 border-b">≥ 0.5 우수</td></tr>
            <tr><td className="px-4 py-2 border-b font-mono">MDD</td><td className="px-4 py-2 border-b">Max Drawdown. 최대 낙폭 (피크 → 저점)</td><td className="px-4 py-2 border-b">낮을수록 ↓</td></tr>
            <tr><td className="px-4 py-2 border-b font-mono">Turnover</td><td className="px-4 py-2 border-b">회전율. 거래비용/슬리피지 비례</td><td className="px-4 py-2 border-b">낮을수록 ↓</td></tr>
            <tr><td className="px-4 py-2 border-b font-mono">Sharpe</td><td className="px-4 py-2 border-b">위험조정 수익률 (수익률/변동성)</td><td className="px-4 py-2 border-b">≥ 1.0 우수</td></tr>
          </tbody>
        </table>
      </section>

      {/* 부록 */}
      <section className="bg-gray-50 border border-gray-200 rounded p-6">
        <h2 className="text-xl font-bold text-gray-900 mb-3">
          부록. 종목별 데이터 행수가 다른 이유
        </h2>
        <p className="text-gray-700 mb-3 text-sm">
          데이터 시드 시 <code className="bg-white px-1.5 py-0.5 rounded text-xs">811 rows</code>, <code className="bg-white px-1.5 py-0.5 rounded text-xs">720 rows</code> 등 종목마다 영업일 수가 다릅니다. 풀 행수(811)는 2023-01-01 ~ 오늘 영업일 수. 부족한 경우의 5가지 이유:
        </p>
        <ul className="list-disc pl-6 text-gray-700 space-y-1 text-sm">
          <li>2023-01-01 이후 IPO/상장</li>
          <li>거래정지/관리종목 기간 (yfinance가 정지 행 누락)</li>
          <li>KOSPI ↔ KOSDAQ 이전 상장 (코드 변경)</li>
          <li>액면분할/합병 후 코드 변경</li>
          <li>yfinance 데이터 자체 누락 (해외 데이터 소스 한계)</li>
        </ul>
        <p className="text-gray-600 mt-3 text-sm">
          → 모델 학습 시 행수 적은 종목은 lookback 미달로 자동 제외. 운영엔 영향 없음.
        </p>
      </section>
    </article>
  );
}
