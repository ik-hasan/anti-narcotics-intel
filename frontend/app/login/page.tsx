"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { formatApiError, useAuth } from "@/lib/auth";
import { PasswordField } from "@/components/PasswordField";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email.trim(), password);
      router.replace("/");
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel auth-card" onSubmit={onSubmit}>
      <p className="kicker">Access</p>
      <h2>Sign in</h2>
      <p className="lede">Use the email and password for your verified account.</p>
      <label className="muted">Email</label>
      <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
      <label className="muted" style={{ marginTop: 12, display: "block" }}>
        Password
      </label>
      <PasswordField
        required
        minLength={8}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="current-password"
      />
      {error && <p className="err">{error}</p>}
      <div className="row" style={{ marginTop: 16 }}>
        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <Link href="/signup" className="muted">
          Create an account
        </Link>
      </div>
    </form>
  );
}
