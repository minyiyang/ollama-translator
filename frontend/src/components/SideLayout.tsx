import { useState, type ReactNode, type Ref } from "react";

function readCollapsed(key: string) {
  try { return localStorage.getItem(key) === "1"; } catch { return false; }
}

/**
 * Page body with a sticky left sidebar that scrolls on its own and can be
 * collapsed to a narrow rail; the choice is remembered per page.
 */
export function SideLayout({
  sidebar,
  children,
  storageKey,
  label,
  navRef,
}: {
  sidebar: ReactNode;
  children: ReactNode;
  storageKey: string;
  label: string;
  navRef?: Ref<HTMLElement>;
}) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(storageKey));
  const toggle = () => {
    setCollapsed((value) => {
      try { localStorage.setItem(storageKey, value ? "0" : "1"); } catch { /* per-tab only */ }
      return !value;
    });
  };
  return (
    <div className={`side-layout ${collapsed ? "collapsed" : ""}`}>
      <aside className="sidenav" ref={navRef} aria-label={label}>
        <button type="button" className="collapse-btn" onClick={toggle} aria-expanded={!collapsed}
          title={collapsed ? `Show ${label.toLowerCase()}` : `Hide ${label.toLowerCase()}`}>
          {collapsed ? "»" : "«"}
          {!collapsed && <span>{label}</span>}
        </button>
        {!collapsed && sidebar}
      </aside>
      <main>{children}</main>
    </div>
  );
}

/** One clickable sidebar row with an optional count on the right. */
export function SideItem({
  active,
  onClick,
  children,
  count,
  sub,
}: {
  active?: boolean;
  onClick: () => void;
  children: ReactNode;
  count?: number;
  sub?: ReactNode;
}) {
  return (
    <button type="button" className={`item ${active ? "active" : ""}`} onClick={onClick}>
      <span className="item-main">
        <span className="item-label">{children}</span>
        {count !== undefined && <span className="count">{count}</span>}
      </span>
      {sub && <span className="ch">{sub}</span>}
    </button>
  );
}
