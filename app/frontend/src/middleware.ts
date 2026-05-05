/**
 * Next.js middleware: gate every page request behind a session cookie.
 *
 * Cookie presence is a UX-only check — the actual JWT signature is verified
 * by the FastAPI backend on each /api/v1/* request. Spoofed cookies still
 * get 401 from the API, this middleware just avoids the flicker of loading
 * a protected page first.
 */

import { NextRequest, NextResponse } from "next/server";

const COOKIE_NAME = "qlib_session";

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // Public paths — login itself plus Next.js asset routes.
  if (
    pathname === "/login" ||
    pathname.startsWith("/login/") ||
    pathname.startsWith("/_next") ||
    pathname === "/favicon.ico"
  ) {
    return NextResponse.next();
  }

  if (!req.cookies.get(COOKIE_NAME)) {
    const loginUrl = req.nextUrl.clone();
    loginUrl.pathname = "/login";
    loginUrl.searchParams.set("next", pathname + req.nextUrl.search);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
