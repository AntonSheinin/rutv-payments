import { errorMessage } from "./api";
import { ArrowUpRight, ShieldCheck } from "lucide-react";
import React, { useState } from "react";
import { api, setCsrf } from "./api";
import type { User } from "./types";
import { ThemeToggle } from "./ThemeToggle";
export function Login({
  onLogin,
  sessionError,
}: {
  onLogin: (u: User) => void;
  sessionError: string;
}) {
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const user = await api<User>("/auth/login", "POST", {
        username,
        password,
      });
      setCsrf(user.csrf);
      onLogin(user);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <div className="login-theme">
        <ThemeToggle />
      </div>
      <div className="login-art">
        <div className="wordmark">
          <img src="/assets/rutv-logo.png" alt="RuTV logo" />
          <span className="brand-name">
            RuTV<small>Payments</small>
          </span>
        </div>
        <h1>
          A clear view of
          <br />
          every payment.
        </h1>
        <p>
          Your internal space for payments, subscriptions,
          <br />
          and the numbers behind them.
        </p>
        <ShieldCheck size={64} strokeWidth={1} />
      </div>
      <form className="login-card" onSubmit={submit}>
        <img
          className="login-logo"
          src="/assets/rutv-logo.png"
          alt="RuTV logo"
        />
        <span className="eyebrow">STAFF ACCESS</span>
        <h2>Welcome back</h2>
        <p>Sign in to payments.</p>
        <label>
          Username
          <input
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {(error || sessionError) && (
          <p role="alert" className="error">
            {error || sessionError}
          </p>
        )}
        <button className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
          <ArrowUpRight size={17} />
        </button>
        <small>Authorized staff only · Payment details stay with PayPal</small>
      </form>
    </main>
  );
}
