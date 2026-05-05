"use client";

import "./globals.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { logout } from "@/lib/auth";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
      })
  );

  const pathname = usePathname();
  const isLoginPage = pathname === "/login";

  async function handleLogout() {
    await logout();
    window.location.href = "/login";
  }

  return (
    <html lang="ko">
      <body className="bg-gray-50 min-h-screen">
        <QueryClientProvider client={queryClient}>
          {!isLoginPage && (
            <nav className="bg-white border-b border-gray-200 px-6 py-3">
              <div className="max-w-7xl mx-auto flex items-center gap-8">
                <Link href="/" className="text-xl font-bold text-blue-600">
                  Qlib Web
                </Link>
                <Link
                  href="/backtest/new"
                  className="text-gray-600 hover:text-blue-600"
                >
                  New Backtest
                </Link>
                <Link
                  href="/backtest/optimize"
                  className="text-gray-600 hover:text-blue-600"
                >
                  Optimize
                </Link>
                <Link href="/live" className="text-gray-600 hover:text-blue-600 font-medium">
                  📊 모의투자
                </Link>
                <Link href="/data" className="text-gray-600 hover:text-blue-600">
                  Market Data
                </Link>
                <Link href="/guide" className="text-gray-600 hover:text-blue-600">
                  📖 매매 가이드
                </Link>
                <button
                  onClick={handleLogout}
                  className="ml-auto text-sm text-gray-500 hover:text-red-600"
                >
                  로그아웃
                </button>
              </div>
            </nav>
          )}
          {isLoginPage ? (
            children
          ) : (
            <main className="max-w-7xl mx-auto px-6 py-8">{children}</main>
          )}
        </QueryClientProvider>
      </body>
    </html>
  );
}
