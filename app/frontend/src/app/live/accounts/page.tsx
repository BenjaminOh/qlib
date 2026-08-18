"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import AccountPolicyForm from "@/components/AccountPolicyForm";

/** 계좌별 주문 방식 설정.
 *
 * 이 화면이 있기 전에는 실주문이 코드에 시장가로 고정돼 있었습니다. 계좌마다
 * 원하는 체결 방식이 다르므로, 배포가 아니라 여기서 바꿉니다.
 */
export default function LiveAccountsPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["live-accounts"],
    queryFn: api.getAccounts,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold text-gray-900">⚙️ 계좌 주문 설정</h1>
        <Link href="/live" className="text-blue-600 hover:underline text-sm">
          ← 대시보드
        </Link>
      </div>

      <p className="text-sm text-gray-600">
        여기서 고른 방식으로 <strong>실계좌(open) 주문</strong>이 나갑니다. 시뮬레이션
        곡선(종가·수급·트레일·사다리·지정가·카페·급등)은 비교 실험이라 이 설정의 영향을
        받지 않습니다.
      </p>

      {isLoading && <p className="text-sm text-gray-400">불러오는 중…</p>}
      {isError && (
        <p className="text-sm text-red-600">{(error as Error).message}</p>
      )}
      {data?.length === 0 && (
        <p className="text-sm text-gray-500">등록된 계좌가 없습니다.</p>
      )}

      {data?.map((a) => (
        <AccountPolicyForm key={a.account_id} account={a} />
      ))}

      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-600 space-y-1.5">
        <p className="font-medium text-gray-800">읽는 법</p>
        <p>
          <strong>시장가</strong> — 즉시 체결되지만 체결가는 사후에 확정됩니다. 갭 상승
          종목을 그대로 추격해 담을 수 있습니다.
        </p>
        <p>
          <strong>지정가</strong> — 기준가에서 정한 만큼 떨어진 가격에 예약합니다. 가격이
          내려오지 않으면 체결되지 않습니다(미체결 리스크). 주문금액 상한도 지정가에서만
          실제로 걸립니다.
        </p>
        <p>
          <strong>미체결 취소 시각</strong> — 이 시각이 지나면 남아 있는 지정가를
          취소해 현금을 풀어 줍니다. 비우면 장 마감까지 그대로 둡니다.
        </p>
      </div>
    </div>
  );
}
