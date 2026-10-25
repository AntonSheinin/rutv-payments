import { CreditCard, LayoutDashboard, LogOut, Repeat, X } from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";
import { labels } from "./ui";
import type { View } from "./ui";

/** Workspace navigation, responsive menu controls and the signed-in staff identity. */
export function Navigation({
  userName,
  view,
  mobileMenuOpen,
  requestPending,
  navigate,
  closeMenu,
  logout,
}: {
  userName: string;
  view: View;
  mobileMenuOpen: boolean;
  requestPending: boolean;
  navigate: (view: View) => void;
  closeMenu: () => void;
  logout: () => Promise<void>;
}) {
  return (
    <aside
      id="main-navigation"
      className={"sidebar" + (mobileMenuOpen ? " open" : "")}
    >
      <div className="sidebar-brand">
        <a className="wordmark" href="#" onClick={() => navigate("dashboard")}>
          <img src="/assets/rutv-logo.png" alt="RuTV logo" />
          <span className="brand-name">RuTV</span>
        </a>
        <ThemeToggle />
        <button
          className="icon-button sidebar-close"
          aria-label="Close menu"
          onClick={() => closeMenu()}
        >
          <X size={18} />
        </button>
      </div>
      <nav aria-label="Main navigation">
        {(
          [
            ["dashboard", LayoutDashboard],
            ["payments", CreditCard],
            ["subscriptions", Repeat],
          ] as const
        ).map(([key, Icon]) => (
          <button
            key={key}
            disabled={requestPending}
            aria-label={labels[key]}
            aria-current={view === key ? "page" : undefined}
            title={labels[key]}
            className={view === key ? "active" : ""}
            onClick={() => navigate(key)}
          >
            <Icon size={20} />
            {labels[key]}
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <span className="profile-name" title={userName}>
          {userName}
        </span>
        <button
          className="logout-button"
          aria-label="Logout"
          title="Logout"
          disabled={requestPending}
          onClick={logout}
        >
          <LogOut size={20} />
        </button>
      </div>
    </aside>
  );
}
