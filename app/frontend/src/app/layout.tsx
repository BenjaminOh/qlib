"use client";

import "./globals.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import Link from "next/link";

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

  return (
    <html lang="ko">
      <body className="bg-gray-50 min-h-screen">
        <QueryClientProvider client={queryClient}>
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
            </div>
          </nav>
          <main className="max-w-7xl mx-auto px-6 py-8">{children}</main>
        </QueryClientProvider>
      </body>
    </html>
  );
}
