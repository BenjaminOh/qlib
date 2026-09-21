"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, AccountCredentialRow, AccountTestResult } from "@/lib/api";

/** 계좌 자격증명 등록 — 지인에게 받은 값을 붙여넣고 연결을 확인하는 화면.
 *
 * 이 화면이 생기기 전에는 계좌를 늘릴 때마다 서버 `.env` 를 고치고 컨테이너를
 * 재시작해야 했습니다. 자격증명이 시차를 두고 들어오는 상황(지인마다 발급 시점이
 * 다름)과 맞지 않았습니다.
 *
 * 시크릿은 **올라가기만** 합니다. 저장 후 입력칸을 비우고, 화면에는 마스킹된
 * 앱키만 남습니다 — 서버도 응답에 시크릿을 싣지 않습니다.
 */

const SLOTS = ["acct1", "acct2", "acct3", "acct4"];

function StepList({ result }: { result: AccountTestResult }) {
  return (
    <div className="mt-2 space-y-1">
      {result.steps.map((s) => (
        <div key={s.step} className="text-xs flex gap-2">
          <span className={s.ok ? "text-emerald-600" : "text-red-600"}>
            {s.ok ? "✓" : "✗"}
          </span>
          <span className="font-medium text-gray-700 shrink-0">{s.step}</span>
          <span className="text-gray-500 break-all">{s.detail}</span>
        </div>
      ))}
      {result.hint && (
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mt-1.5">
          💡 {result.hint}
        </p>
      )}
    </div>
  );
}

/** 매매 방식 선택 — 계좌가 무엇을 어떻게 살지 정한다.
 *
 * 비워두면(=선택 안 함) 그 계좌는 주문을 내지 않습니다. 자격증명만 먼저 등록하고
 * 방식은 나중에 정하는 흐름을 그대로 허용합니다.
 */
function StrategyPicker({ row }: { row: AccountCredentialRow }) {
  const qc = useQueryClient();
  const { data: tpl } = useQuery({
    queryKey: ["templates"],
    queryFn: api.getTemplates,
    staleTime: 60 * 60 * 1000,   // 코드에 선언된 목록이라 자주 안 바뀐다
  });

  const initialRet20 = (() => {
    try {
      return row.strategy_params
        ? String(JSON.parse(row.strategy_params).ret20_max ?? "")
        : "";
    } catch {
      return "";
    }
  })();

  const [template, setTemplate] = useState(row.template ?? "");
  const [ret20, setRet20] = useState(initialRet20);

  const save = useMutation({
    mutationFn: () =>
      api.putAccountStrategy(row.account_id, {
        template: template || null,
        ret20_max: template === "cafe" && ret20 ? Number(ret20) : null,
        strategy_enabled: true,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["account-credentials"] }),
  });

  const dirty =
    (template || null) !== (row.template ?? null) ||
    (template === "cafe" && ret20 !== initialRet20);

  return (
    <div className="mt-2 flex flex-wrap items-end gap-2 border-t border-gray-100 pt-2">
      <label className="text-xs text-gray-600">
        매매 방식
        <select
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          className="mt-0.5 block rounded border border-gray-300 px-2 py-1 text-xs"
        >
          <option value="">선택 안 함 (주문 없음)</option>
          {tpl?.templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
              {t.slot ? ` · ${t.slot}` : ""}
            </option>
          ))}
        </select>
      </label>

      {template === "cafe" && (
        <label className="text-xs text-gray-600">
          ret20 상한(%)
          <input
            value={ret20}
            onChange={(e) => setRet20(e.target.value)}
            placeholder="비우면 제한 없음"
            className="mt-0.5 block w-32 rounded border border-gray-300 px-2 py-1 text-xs"
          />
        </label>
      )}

      <button
        onClick={() => save.mutate()}
        disabled={!dirty || save.isPending}
        className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-40"
      >
        {save.isPending ? "저장 중…" : "방식 저장"}
      </button>

      {save.isError && (
        <span className="text-xs text-red-600">
          {(save.error as Error).message}
        </span>
      )}
      {save.isSuccess && !dirty && (
        <span className="text-xs text-emerald-700">저장됨</span>
      )}
    </div>
  );
}

function AccountCard({ row }: { row: AccountCredentialRow }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [appKey, setAppKey] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [acctNo, setAcctNo] = useState(row.account_no ?? "");
  const [label, setLabel] = useState(row.label ?? "");
  const [note, setNote] = useState(row.note ?? "");
  const [test, setTest] = useState<AccountTestResult | null>(null);

  const save = useMutation({
    mutationFn: () =>
      api.putAccountCredentials(row.account_id, {
        app_key: appKey.trim(),
        app_secret: appSecret.trim(),
        account_no: acctNo.trim(),
        kis_env: "paper",
        label: label.trim() || null,
        note: note.trim() || null,
      }),
    onSuccess: () => {
      // 시크릿은 화면에 남기지 않는다
      setAppKey("");
      setAppSecret("");
      setTest(null);
      qc.invalidateQueries({ queryKey: ["account-credentials"] });
    },
  });

  const runTest = useMutation({
    mutationFn: () => api.testAccountConnection(row.account_id),
    onSuccess: (r) => setTest(r),
  });

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <span className="font-semibold text-gray-900">
            {row.label || row.account_id}
          </span>
          <span className="ml-2 text-xs text-gray-400">{row.account_id}</span>
        </div>
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${
            row.source === "db"
              ? "bg-emerald-50 text-emerald-700"
              : "bg-gray-100 text-gray-600"
          }`}
        >
          {row.source === "db" ? "웹에서 등록됨" : "서버 설정(.env)"}
        </span>
      </div>

      <div className="mt-1.5 text-xs text-gray-600 tabular-nums">
        {row.app_key_masked ? (
          <>
            앱키 <code className="bg-gray-50 px-1 rounded">{row.app_key_masked}</code>
            {" · "}계좌 {row.account_no}
            {row.kis_env && ` · ${row.kis_env}`}
          </>
        ) : (
          <span className="text-gray-400">자격증명 미등록</span>
        )}
        {row.note && <span className="text-gray-400"> · {row.note}</span>}
      </div>

      <div className="mt-2 flex gap-2">
        <button
          onClick={() => setOpen((v) => !v)}
          className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
        >
          {open ? "닫기" : row.app_key_masked ? "자격증명 교체" : "자격증명 등록"}
        </button>
        {row.app_key_masked && (
          <button
            onClick={() => runTest.mutate()}
            disabled={runTest.isPending}
            className="rounded border border-emerald-300 px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
          >
            {runTest.isPending ? "확인 중…" : "연결 테스트"}
          </button>
        )}
      </div>

      {/* 자격증명이 붙은 계좌만 방식을 고른다 — 계좌 없이 방식부터 정하는 것은
          순서가 뒤집힌 것이고, 어차피 주문이 나가지 않는다. */}
      {row.app_key_masked && <StrategyPicker row={row} />}

      {test && <StepList result={test} />}
      {runTest.isError && (
        <p className="mt-2 text-xs text-red-600">
          {(runTest.error as Error).message}
        </p>
      )}

      {open && (
        <div className="mt-3 space-y-2 border-t border-gray-100 pt-3">
          <label className="block text-xs text-gray-600">
            APP KEY (36자)
            <input
              value={appKey}
              onChange={(e) => setAppKey(e.target.value)}
              placeholder="PSYuRT17…"
              className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1 text-xs font-mono"
            />
          </label>
          <label className="block text-xs text-gray-600">
            APP SECRET (180자 안팎 · 줄바꿈 없이 한 줄)
            <textarea
              value={appSecret}
              onChange={(e) => setAppSecret(e.target.value)}
              rows={3}
              className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1 text-xs font-mono"
            />
          </label>
          <div className="flex gap-2">
            <label className="block text-xs text-gray-600 flex-1">
              모의계좌 번호
              <input
                value={acctNo}
                onChange={(e) => setAcctNo(e.target.value)}
                placeholder="12345678-01"
                className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1 text-xs font-mono"
              />
            </label>
            <label className="block text-xs text-gray-600 flex-1">
              표시 이름
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="지인 A 계좌"
                className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1 text-xs"
              />
            </label>
          </div>
          <label className="block text-xs text-gray-600">
            메모
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="2026-09 수령 · 앱키 만료 2027-09"
              className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1 text-xs"
            />
          </label>

          <div className="flex items-center gap-2">
            <button
              onClick={() => save.mutate()}
              disabled={save.isPending || !appKey || !appSecret || !acctNo}
              className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-40"
            >
              {save.isPending ? "저장 중…" : "저장"}
            </button>
            <span className="text-[11px] text-gray-400">
              저장하면 재시작 없이 적용됩니다. 저장 뒤 연결 테스트를 눌러 확인하세요.
            </span>
          </div>
          {save.isError && (
            <p className="text-xs text-red-600">{(save.error as Error).message}</p>
          )}
          {save.isSuccess && (
            <p className="text-xs text-emerald-700">
              저장했습니다 — 이제 [연결 테스트]로 확인하세요.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function AccountCredentialsPanel() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["account-credentials"],
    queryFn: api.getAccountCredentials,
  });

  if (isLoading) return <p className="text-sm text-gray-400">불러오는 중…</p>;
  if (isError)
    return <p className="text-sm text-red-600">{(error as Error).message}</p>;
  if (!data) return null;

  // DB·env 에 이미 있는 계좌 + 아직 비어 있는 슬롯을 함께 보여준다.
  const known = new Set(data.accounts.map((a) => a.account_id));
  const emptySlots: AccountCredentialRow[] = SLOTS.filter((s) => !known.has(s)).map(
    (s) => ({
      account_id: s, label: null, source: "env", app_key_masked: null,
      account_no: null, kis_env: null, enabled: true, note: null,
      template: null, strategy_params: null, strategy_enabled: true,
    }),
  );

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-gray-900">🔑 계좌 자격증명</h2>
        <span className="text-xs text-gray-500">
          저장 후 재시작 없이 적용 · 시크릿은 화면에 다시 표시되지 않습니다
        </span>
      </div>

      {!data.secrets_key_configured && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <strong>서버에 암호화 키가 없습니다.</strong> 자격증명을 평문으로 저장하지
          않기 위해 등록이 거부됩니다. 서버 <code>.env</code> 에{" "}
          <code>QLIB_API_SECRETS_KEY</code> 를 먼저 넣어주세요.
        </div>
      )}

      {[...data.accounts, ...emptySlots].map((a) => (
        <AccountCard key={a.account_id} row={a} />
      ))}
    </section>
  );
}
