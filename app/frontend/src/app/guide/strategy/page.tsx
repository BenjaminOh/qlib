import { redirect } from "next/navigation";

/** 구 "전략 설명서" — 통합 가이드(/guide)로 합쳐졌다. 구 링크 보존용 리다이렉트. */
export default function StrategyGuideRedirect() {
  redirect("/guide#strategies");
}
