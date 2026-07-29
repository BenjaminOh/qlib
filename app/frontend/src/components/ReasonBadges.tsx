"use client";

import { SignalReasons } from "@/lib/api";

export function Badge({ label, tone }: { label: string; tone: "up" | "down" | "flat" }) {
  const cls =
    tone === "up"
      ? "bg-red-50 text-red-700 border-red-200"
      : tone === "down"
        ? "bg-blue-50 text-blue-700 border-blue-200"
        : "bg-gray-50 text-gray-600 border-gray-200";
  return (
    <span className={`inline-block text-[11px] px-1.5 py-0.5 rounded border mr-1 mb-1 ${cls}`}>
      {label}
    </span>
  );
}

export function MetricBadges({ m }: { m: SignalReasons["metrics"] }) {
  const badges: JSX.Element[] = [];
  if (m.ret5 != null)
    badges.push(<Badge key="r5" label={`5일 ${m.ret5 > 0 ? "+" : ""}${m.ret5}%`} tone={m.ret5 > 0 ? "up" : m.ret5 < 0 ? "down" : "flat"} />);
  if (m.ret20 != null)
    badges.push(<Badge key="r20" label={`20일 ${m.ret20 > 0 ? "+" : ""}${m.ret20}%`} tone={m.ret20 > 0 ? "up" : m.ret20 < 0 ? "down" : "flat"} />);
  if (m.vol_ratio != null && m.vol_ratio >= 1.5)
    badges.push(<Badge key="vr" label={`거래량 ${m.vol_ratio}배`} tone="up" />);
  if (m.ma20_gap != null)
    badges.push(<Badge key="ma" label={`20일선 ${m.ma20_gap > 0 ? "+" : ""}${m.ma20_gap}%`} tone={m.ma20_gap > 0 ? "up" : "down"} />);
  if (m.high60_pos != null)
    badges.push(<Badge key="hp" label={`60일고점 ${m.high60_pos}%`} tone={m.high60_pos >= -2 ? "up" : "flat"} />);
  return <div className="flex flex-wrap">{badges}</div>;
}

export function FeatureContribList({ features }: { features: SignalReasons["top_features"] }) {
  if (!features?.length) return null;
  return (
    <div className="space-y-1">
      {features.map((f) => {
        const width = Math.min(Math.abs(f.contrib) * 2000, 100);
        return (
          <div key={f.name} className="flex items-center gap-2 text-xs">
            <span className="w-44 shrink-0 text-gray-700">{f.desc}</span>
            <span className="font-mono text-[10px] text-gray-400 w-16 shrink-0">{f.name}</span>
            <div className="flex-1 h-2 bg-gray-100 rounded overflow-hidden">
              <div
                className={`h-full ${f.contrib >= 0 ? "bg-red-400" : "bg-blue-400"}`}
                style={{ width: `${width}%` }}
              />
            </div>
            <span className={`font-mono w-16 text-right ${f.contrib >= 0 ? "text-red-600" : "text-blue-600"}`}>
              {f.contrib >= 0 ? "+" : ""}{f.contrib.toFixed(4)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
