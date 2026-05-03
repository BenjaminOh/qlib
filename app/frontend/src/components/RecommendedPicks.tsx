import { RecommendedPick } from "@/lib/api";

export default function RecommendedPicks({
  picks,
  topk,
}: {
  picks: RecommendedPick[];
  topk?: number;
}) {
  if (!picks || picks.length === 0) return null;
  const asOf = picks[0]?.as_of;

  return (
    <section className="bg-emerald-50 border border-emerald-200 rounded-lg p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-lg font-semibold text-emerald-900">
          📌 다음 거래일 추천 보유 종목 (Top {picks.length})
        </h2>
        <span className="text-xs text-emerald-700">
          기준일 {asOf} · {topk != null ? `topk=${topk}` : ""}
        </span>
      </div>
      <p className="text-xs text-emerald-800/80 mb-4">
        모델이 마지막 거래일 시점에 매긴 알파 점수 상위 {picks.length}개 종목입니다.
        이 백테스트 설정을 그대로 운용한다면 다음 거래일에 보유해야 할 종목 후보입니다.
        실제 매수 전 호가/거래정지/이슈를 별도로 확인하세요.
      </p>
      <div className="overflow-x-auto bg-white rounded-md border border-emerald-100">
        <table className="w-full text-sm">
          <thead className="bg-emerald-50 text-emerald-900">
            <tr>
              <th className="text-left px-3 py-2 font-medium">순위</th>
              <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
              <th className="text-right px-3 py-2 font-medium">알파 점수</th>
              <th className="text-left px-3 py-2 font-medium">조회</th>
            </tr>
          </thead>
          <tbody>
            {picks.map((p) => (
              <tr key={p.code} className="border-t border-emerald-50">
                <td className="px-3 py-2 font-semibold text-emerald-700">#{p.rank}</td>
                <td className="px-3 py-2">
                  <span className="font-medium text-gray-900">{p.name ?? p.code}</span>
                  {p.name && p.name !== p.code && (
                    <span className="font-mono text-xs text-gray-500 ml-2">({p.code})</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {p.score == null ? "—" : p.score.toFixed(6)}
                </td>
                <td className="px-3 py-2">
                  <a
                    href={`https://finance.naver.com/item/main.naver?code=${p.code}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline text-xs"
                  >
                    네이버 금융 ↗
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
