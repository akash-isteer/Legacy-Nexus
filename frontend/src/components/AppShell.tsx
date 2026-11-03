import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  LuBookOpen, LuChevronUp, LuCircleHelp, LuFileCode2, LuFileText, LuFolderKanban, LuGitFork, LuLayoutDashboard,
  LuLogOut, LuMessageSquareText, LuPanelLeftClose, LuPanelLeftOpen, LuRadar, LuRocket, LuScale, LuSettings, LuUserRound, LuWorkflow,
} from "react-icons/lu";
import { useAuth } from "../lib/auth";
import { initials } from "../lib/format";
import { Link, navigate, useLocation } from "../lib/router";
import { useOptionalProject } from "./ProjectContext";
import { useTour } from "./Tour";
import { Logo } from "./ui";

type Page = { path: string; label: string; icon: typeof LuLayoutDashboard };

export const PROJECT_GROUPS: { title: string; pages: Page[] }[] = [
  {
    title: "Understand",
    pages: [
      { path: "", label: "Dashboard", icon: LuLayoutDashboard },
      { path: "graph", label: "Dependency map", icon: LuGitFork },
      { path: "rules", label: "Business rules", icon: LuScale },
      { path: "ask", label: "Ask the code", icon: LuMessageSquareText },
      { path: "docs", label: "Documentation", icon: LuBookOpen },
    ],
  },
  {
    title: "Analyse",
    pages: [
      { path: "impact", label: "Impact analysis", icon: LuRadar },
      { path: "lineage", label: "Data lineage", icon: LuWorkflow },
      { path: "files", label: "Source files", icon: LuFileCode2 },
    ],
  },
  { title: "Plan", pages: [{ path: "modernize", label: "Modernization", icon: LuRocket }] },
];

export const PROJECT_PAGES: Page[] = PROJECT_GROUPS.flatMap((g) => g.pages);

const ROLE_LABEL: Record<string, string> = { admin: "Admin", analyst: "Analyst", viewer: "Viewer" };

export function AppShell({ title, actions, children, wide }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; wide?: boolean }) {
  const { path } = useLocation();
  const { replay, available: guideAvailable } = useTour();
  const ctx = useOptionalProject();
  const project = ctx?.project;
  const base = project ? `/projects/${project.id}` : "";
  const { closed, mobileOpen, toggle, closeMobile } = useSidebar();

  useEffect(closeMobile, [path]); // leaving a page closes the phone overlay

  return (
    <div className={`shell ${closed ? "nav-closed" : ""} ${mobileOpen ? "nav-mobile-open" : ""}`}>
      {mobileOpen && <div className="nav-scrim" onClick={closeMobile} />}
      <aside className="sidebar">
        <Link to="/projects" className="brand" style={{ textDecoration: "none" }}>
          <Logo size={30} />
          <span>LegacyNexus<small>Application intelligence</small></span>
        </Link>
        <div className="nav-group" style={{ paddingTop: 4 }}>Workspace</div>
        <Link to="/projects" className={`nav-item ${path === "/projects" ? "active" : ""}`} data-tour="nav-projects">
          <LuFolderKanban size={17} /> Applications
        </Link>
        <Link to="/doc-qa" className={`nav-item ${path === "/doc-qa" ? "active" : ""}`}>
          <LuFileText size={17} /> Document Q&amp;A
        </Link>
        {project && (
          <>
            <div className="project-card" style={{ marginTop: 14 }}>
              <div className="name" title={project.name}>{project.name}</div>
              <div style={{ fontSize: 12, color: "var(--ink-muted)" }}>
                {project.status === "ready" ? `${project.stats.programs_analysed ?? 0} components · ready` : project.status}
              </div>
            </div>
            {PROJECT_GROUPS.map((g) => (
              <div key={g.title}>
                <div className="nav-group">{g.title}</div>
                {g.pages.map((p) => {
                  const to = p.path ? `${base}/${p.path}` : base;
                  const disabled = project.status !== "ready" && p.path !== "files" && p.path !== "";
                  return disabled ? (
                    <span key={p.path} className="nav-item" style={{ opacity: 0.4, cursor: "not-allowed" }} title="Available when the analysis is ready">
                      <p.icon size={17} /> {p.label}
                    </span>
                  ) : (
                    <Link key={p.path} to={to} className={`nav-item ${path === to ? "active" : ""}`}>
                      <p.icon size={17} /> {p.label}
                    </Link>
                  );
                })}
              </div>
            ))}
          </>
        )}
        <div className="foot">
          <Link to="/settings" className={`nav-item ${path === "/settings" ? "active" : ""}`} data-tour="nav-settings">
            <LuSettings size={17} /> Settings
          </Link>
          <UserMenu />
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <button className="btn btn-sm btn-ghost nav-toggle" onClick={toggle} aria-label="Show or hide the menu"
            title={closed ? "Show menu (Ctrl+B)" : "Hide menu (Ctrl+B)"}>
            {closed ? <LuPanelLeftOpen size={18} /> : <LuPanelLeftClose size={18} />}
          </button>
          <nav className="crumbs" aria-label="Breadcrumb">
            {project ? (
              <>
                <Link to="/projects">Applications</Link> <span>/</span>
                <Link to={base}><b>{project.name}</b></Link>
                {title && <><span>/</span><b>{title}</b></>}
              </>
            ) : (
              <b>{title}</b>
            )}
          </nav>
          <div className="spacer" />
          {actions}
          {guideAvailable && (
            <button className="btn btn-sm btn-ghost" onClick={replay} data-tour="nav-guide" title="Show the guide for this page">
              <LuCircleHelp size={15} /> Guide
            </button>
          )}
        </header>
        <div className={`content ${wide ? "content-wide" : ""}`}>{children}</div>
      </main>
    </div>
  );
}

const NAV_KEY = "legacynexus.sidebarClosed";
const isPhone = () => window.matchMedia("(max-width: 900px)").matches;

/** Main menu visibility. On wide screens the choice is remembered across pages; on phones it opens as an overlay. */
function useSidebar() {
  const [closed, setClosed] = useState(() => {
    try { return localStorage.getItem(NAV_KEY) === "1"; } catch { return false; }
  });
  const [mobileOpen, setMobileOpen] = useState(false);

  const toggle = () => {
    if (isPhone()) { setMobileOpen((o) => !o); return; }
    setClosed((c) => {
      try { localStorage.setItem(NAV_KEY, c ? "0" : "1"); } catch { /* storage unavailable: still toggles */ }
      return !c;
    });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") { e.preventDefault(); toggle(); }
      if (e.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Charts and graphs size themselves on window resize; nudge them when the main area changes width.
  useEffect(() => { window.dispatchEvent(new Event("resize")); }, [closed]);

  return { closed, mobileOpen, toggle, closeMobile: () => setMobileOpen(false) };
}

function UserMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  if (!user) return null;
  const go = (to: string) => {
    setOpen(false);
    navigate(to);
  };
  return (
    <div className="user-menu" ref={ref}>
      {open && (
        <div className="menu-pop" role="menu">
          <button className="menu-item" role="menuitem" onClick={() => go("/settings?tab=account")}><LuUserRound size={16} /> My account</button>
          <button className="menu-item" role="menuitem" onClick={() => go("/settings")}><LuSettings size={16} /> Settings</button>
          <div className="divider" />
          <button className="menu-item" role="menuitem" onClick={logout}><LuLogOut size={16} /> Sign out</button>
        </div>
      )}
      <button className="user-chip clickable" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}>
        <span className="avatar">{initials(user.full_name || user.email)}</span>
        <span className="who">
          {user.full_name}<span className="role-badge">{ROLE_LABEL[user.role] ?? user.role}</span>
          <small>{user.email}</small>
        </span>
        <LuChevronUp size={15} style={{ marginLeft: "auto", color: "var(--ink-muted)", transform: open ? "none" : "rotate(180deg)" }} />
      </button>
    </div>
  );
}
