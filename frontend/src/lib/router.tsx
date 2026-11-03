/** Minimal history-API router: path matching, navigation and links. */
import { createContext, useContext, useEffect, useState, type AnchorHTMLAttributes, type ReactNode } from "react";

type RouterState = { path: string; search: string };
const RouterContext = createContext<RouterState>({ path: "/", search: "" });

export function navigate(to: string, replace = false) {
  if (replace) window.history.replaceState(null, "", to);
  else window.history.pushState(null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const read = () => ({ path: window.location.pathname, search: window.location.search });
  const [state, setState] = useState<RouterState>(read);
  useEffect(() => {
    const onPop = () => setState(read());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return <RouterContext.Provider value={state}>{children}</RouterContext.Provider>;
}

export function useLocation() {
  return useContext(RouterContext);
}

export function useSearchParam(name: string): string | null {
  const { search } = useLocation();
  return new URLSearchParams(search).get(name);
}

/** Match "/projects/:id/ask" against a path. Returns params or null. */
export function matchPath(pattern: string, path: string): Record<string, string> | null {
  const p = pattern.split("/").filter(Boolean);
  const s = path.split("/").filter(Boolean);
  if (p.length !== s.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < p.length; i++) {
    if (p[i].startsWith(":")) params[p[i].slice(1)] = decodeURIComponent(s[i]);
    else if (p[i] !== s[i]) return null;
  }
  return params;
}

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { to: string };
export function Link({ to, onClick, ...rest }: LinkProps) {
  return (
    <a
      href={to}
      onClick={(e) => {
        onClick?.(e);
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        navigate(to);
      }}
      {...rest}
    />
  );
}
