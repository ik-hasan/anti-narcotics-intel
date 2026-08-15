"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";

const LINKS = [
  { href: "/", label: "Ask" },
  { href: "/graph", label: "Network" },
  { href: "/cases", label: "Cases" },
  { href: "/risk", label: "Risk flags" },
  { href: "/discover", label: "OSINT", admin: true },
  { href: "/ingest", label: "Ingest", admin: true },
];

export function Nav() {
  const path = usePathname();
  const { user, logout } = useAuth();
  const isAdmin = user?.role === "admin";
  return (
    <aside className="nav">
      <div className="brand">
        <div className="mark">
          <span>NG</span>
        </div>
        <div>
          <h1>Narco-Graph</h1>
          <p>Intel console</p>
        </div>
      </div>
      {LINKS.filter((link) => !link.admin || isAdmin).map((link) => (
        <Link key={link.href} href={link.href} className={path === link.href ? "active" : ""}>
          {link.label}
        </Link>
      ))}
      <div className="grow" />
      {user && (
        <div className="nav-user">
          <div className="nav-user-name">{user.name}</div>
          <div className="muted">{user.role}</div>
          <button type="button" className="secondary" onClick={logout}>
            Sign out
          </button>
        </div>
      )}
      <div className="meta">
        {isAdmin
          ? "Admins can query the graph, crawl public reporting, and ingest sources."
          : "Analysts query the existing graph only. Web crawl and ingest are admin-only."}{" "}
        Flags are not findings of guilt.
      </div>
    </aside>
  );
}
