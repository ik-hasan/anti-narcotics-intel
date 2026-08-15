"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { Nav } from "@/components/Nav";
import { useAuth } from "@/lib/auth";

const PUBLIC = new Set(["/login", "/signup", "/verify"]);
const ADMIN_ONLY = new Set(["/discover", "/ingest"]);

export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { user, hydrated } = useAuth();
  const isPublic = PUBLIC.has(path);

  useEffect(() => {
    if (!hydrated) return;
    if (!user && !isPublic) router.replace("/login");
    if (user && isPublic) router.replace("/");
    if (user && user.role !== "admin" && ADMIN_ONLY.has(path)) router.replace("/");
  }, [hydrated, user, isPublic, path, router]);

  if (!hydrated) {
    return (
      <div className="auth-shell">
        <div className="spinner" />
      </div>
    );
  }

  if (isPublic) {
    return <div className="auth-shell">{children}</div>;
  }

  if (!user) {
    return (
      <div className="auth-shell">
        <div className="spinner" />
      </div>
    );
  }

  if (user.role !== "admin" && ADMIN_ONLY.has(path)) {
    return (
      <div className="auth-shell">
        <div className="spinner" />
      </div>
    );
  }

  return (
    <div className="shell">
      <Nav />
      <main className="main">{children}</main>
    </div>
  );
}
