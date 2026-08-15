"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError, setAuthToken } from "@/lib/api";

const TOKEN_KEY = "narcograph.jwt";

export type AuthUser = {
  id: string;
  name: string;
  email: string;
  role: "admin" | "user";
  verified: boolean;
};

type AuthContextValue = {
  user: AuthUser | null;
  hydrated: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (payload: { name: string; email: string; password: string; role: string }) => Promise<string>;
  verifyOtp: (email: string, otp: string) => Promise<void>;
  resendOtp: (email: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function applyToken(token: string | null) {
  setAuthToken(token);
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [hydrated, setHydrated] = useState(false);

  const logout = useCallback(() => {
    applyToken(null);
    setUser(null);
  }, []);

  useEffect(() => {
    const stored = localStorage.getItem(TOKEN_KEY);
    if (!stored) {
      setHydrated(true);
      return;
    }
    applyToken(stored);
    api<{ user: AuthUser }>("/api/auth/me")
      .then((payload) => setUser(payload.user))
      .catch(() => {
        applyToken(null);
        setUser(null);
      })
      .finally(() => setHydrated(true));
  }, []);

  useEffect(() => {
    const onUnauthorized = () => logout();
    window.addEventListener("narcograph:unauthorized", onUnauthorized);
    return () => window.removeEventListener("narcograph:unauthorized", onUnauthorized);
  }, [logout]);

  const login = useCallback(async (email: string, password: string) => {
    const payload = await api<{ token: string; user: AuthUser }>(
      "/api/auth/login",
      { method: "POST", body: JSON.stringify({ email, password }) },
      20000,
    );
    applyToken(payload.token);
    setUser(payload.user);
  }, []);

  const signup = useCallback(async (body: { name: string; email: string; password: string; role: string }) => {
    const payload = await api<{ email: string }>(
      "/api/auth/signup",
      { method: "POST", body: JSON.stringify(body) },
      20000,
    );
    return payload.email;
  }, []);

  const verifyOtp = useCallback(async (email: string, otp: string) => {
    const payload = await api<{ token: string; user: AuthUser }>(
      "/api/auth/verify-otp",
      { method: "POST", body: JSON.stringify({ email, otp }) },
      20000,
    );
    applyToken(payload.token);
    setUser(payload.user);
  }, []);

  const resendOtp = useCallback(async (email: string) => {
    await api("/api/auth/resend-otp", { method: "POST", body: JSON.stringify({ email }) }, 20000);
  }, []);

  const value = useMemo(
    () => ({ user, hydrated, login, signup, verifyOtp, resendOtp, logout }),
    [user, hydrated, login, signup, verifyOtp, resendOtp, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Request failed";
}
