"use client";

import { FormEvent, Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { formatApiError, useAuth } from "@/lib/auth";

function VerifyForm() {
  const { verifyOtp, resendOtp } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setEmail(params.get("email") || sessionStorage.getItem("narcograph.pendingEmail") || "");
  }, [params]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await verifyOtp(email.trim(), otp.trim());
      sessionStorage.removeItem("narcograph.pendingEmail");
      router.replace("/");
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function onResend() {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await resendOtp(email.trim());
      setNote("A new code was sent.");
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel auth-card" onSubmit={onSubmit}>
      <p className="kicker">Access</p>
      <h2>Verify email</h2>
      <p className="lede">Enter the 6-digit OTP. If SMTP is not configured, the code is in the API terminal log.</p>
      <label className="muted">Email</label>
      <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      <label className="muted" style={{ marginTop: 12, display: "block" }}>
        OTP
      </label>
      <input
        required
        inputMode="numeric"
        pattern="[0-9]{6}"
        maxLength={6}
        value={otp}
        onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
        placeholder="000000"
      />
      {note && <p className="muted">{note}</p>}
      {error && <p className="err">{error}</p>}
      <div className="row" style={{ marginTop: 16 }}>
        <button type="submit" disabled={busy || otp.length !== 6}>
          {busy ? "Verifying…" : "Verify and continue"}
        </button>
        <button type="button" className="secondary" disabled={busy || !email} onClick={() => void onResend()}>
          Resend OTP
        </button>
        <Link href="/login" className="muted">
          Sign in
        </Link>
      </div>
    </form>
  );
}

export default function VerifyPage() {
  return (
    <Suspense
      fallback={
        <div className="panel auth-card">
          <div className="spinner" />
        </div>
      }
    >
      <VerifyForm />
    </Suspense>
  );
}
