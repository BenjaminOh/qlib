"use client";

import { chartUrl } from "@/lib/api";

/** Tiny TradingView chart link, safe inside clickable rows. */
export default function ChartLink({ code, className = "" }: { code: string; className?: string }) {
  return (
    <a
      href={chartUrl(code)}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      className={`text-[11px] text-blue-500 hover:underline whitespace-nowrap ${className}`}
      title="트레이딩뷰 차트 열기"
    >
      차트↗
    </a>
  );
}
