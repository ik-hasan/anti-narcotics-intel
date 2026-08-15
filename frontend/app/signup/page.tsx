"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { formatApiError, useAuth } from "@/lib/auth";
import { PasswordField } from "@/components/PasswordField";

export default function SignupPage() {
  const { signup } = useAuth();
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const savedEmail = await signup({ name: name.trim(), email: email.trim(), password, role });
      sessionStorage.setItem("narcograph.pendingEmail", savedEmail);
      router.push("/verify?email=" + encodeURIComponent(savedEmail));
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel auth-card" onSubmit={onSubmit}>
      <p className="kicker">Access</p>
      <h2>Create account</h2>
      <p className="lede">You will receive a 6-digit OTP to verify the email before you can sign in.</p>
      <label className="muted">Name</label>
      <input required minLength={2} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
      <label className="muted" style={{ marginTop: 12, display: "block" }}>
        Email
      </label>
      <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
      <label className="muted" style={{ marginTop: 12, display: "block" }}>
        Password
      </label>
      <PasswordField
        required
        minLength={8}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="new-password"
      />
      <label className="muted" style={{ marginTop: 12, display: "block" }}>
        Role
      </label>
      <select value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="user">user</option>
        <option value="admin">admin</option>
      </select>
      <p className="muted" style={{ marginTop: 8 }}>
        Choose <strong>admin</strong> for full access (Ask, graph, OSINT crawl, ingest). Choose{" "}
        <strong>user</strong> to query the existing graph only. Multiple admins are allowed.
      </p>
      {error && <p className="err">{error}</p>}
      <div className="row" style={{ marginTop: 16 }}>
        <button type="submit" disabled={busy}>
          {busy ? "Sending OTP…" : "Send OTP"}
        </button>
        <Link href="/login" className="muted">
          Already have an account
        </Link>
      </div>
    </form>
  );
}
