"use client";

/**
 * /login — Admin login form.
 *
 * On success, the FastAPI backend sets the httpOnly qlib_session cookie and
 * we redirect to ?next=<original-path> (or `/` if absent). On failure, the
 * 401 response detail is shown inline.
 */

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { login } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
      // Hard navigation so middleware sees the new cookie immediately.
      window.location.href = next;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "로그인 실패";
      setError(msg);
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center px-4">
      <div className="w-full max-w-md bg-white rounded-lg shadow-md p-8">
        <div className="text-center mb-6">
          <div className="text-3xl mb-2">📊</div>
          <h1 className="text-2xl font-bold text-gray-900">Qlib 관리자 로그인</h1>
          <p className="text-sm text-gray-500 mt-1">인증된 사용자만 접근할 수 있습니다.</p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label htmlFor="username" className="block text-sm font-medium text-gray-700 mb-1">
              아이디
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={loading}
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
              autoFocus
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
              비밀번호
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
            />
          </div>

          {error && (
            <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !username || !password}
            className="w-full bg-blue-600 text-white py-2 rounded-md hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors font-medium"
          >
            {loading ? "로그인 중..." : "로그인"}
          </button>
        </form>

        <div className="mt-6 text-center text-xs text-gray-400">
          세션은 24시간 유지됩니다.
        </div>
      </div>
    </div>
  );
}
