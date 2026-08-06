import { tossChartUrl } from "@/lib/api";

/** Tiny Toss Securities chart link, safe inside clickable rows. */
export default function TossLink({ code, className = "" }: { code: string; className?: string }) {
  return (
    <a
      href={tossChartUrl(code)}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      className={`text-[11px] text-blue-500 hover:underline whitespace-nowrap ${className}`}
      title="토스증권 차트 열기"
    >
      토스↗
    </a>
  );
}
