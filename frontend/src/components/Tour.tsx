import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useAuth } from "../lib/auth";

export type TourStep = { target: string; title: string; body: string };
type TourState = {
  start: (key: string, steps: TourStep[], force?: boolean) => void;
  register: (key: string, steps: TourStep[]) => void;
  unregister: (key: string) => void;
  replay: () => void;
  active: boolean;
  /** True when the page on screen has its own guide (the Guide button is shown only then). */
  available: boolean;
};

const TourContext = createContext<TourState>({
  start: () => {}, register: () => {}, unregister: () => {}, replay: () => {}, active: false, available: false,
});
export const useTour = () => useContext(TourContext);

/** Register a page's guide; it starts automatically the first time the user sees the page. */
export function usePageTour(key: string, steps: TourStep[], ready = true) {
  const { start, register, unregister, active } = useTour();
  useEffect(() => {
    register(key, steps);
    return () => unregister(key); // leaving the page: its guide must not replay on the next page
  }, [key, steps, register, unregister]);
  useEffect(() => {
    if (!ready || active) return;
    const t = setTimeout(() => start(key, steps), 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, ready]);
}

const MARGIN = 16; // never closer than this to the screen edge
const GAP = 14; // space between the highlighted element and the card

/** Put the card next to the highlighted element where it fully fits; otherwise keep it inside the screen. */
function placeCard(rect: DOMRect | null, size: { w: number; h: number }): CSSProperties {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const clampTop = (t: number) => Math.min(Math.max(MARGIN, t), Math.max(MARGIN, vh - size.h - MARGIN));
  const clampLeft = (l: number) => Math.min(Math.max(MARGIN, l), Math.max(MARGIN, vw - size.w - MARGIN));
  if (!rect) return { top: clampTop((vh - size.h) / 2), left: clampLeft((vw - size.w) / 2) };

  const room = { below: vh - rect.bottom - GAP - MARGIN, above: rect.top - GAP - MARGIN, right: vw - rect.right - GAP - MARGIN, left: rect.left - GAP - MARGIN };
  if (room.below >= size.h) return { top: rect.bottom + GAP, left: clampLeft(rect.left) };
  if (room.above >= size.h) return { top: rect.top - GAP - size.h, left: clampLeft(rect.left) };
  if (room.right >= size.w) return { top: clampTop(rect.top), left: rect.right + GAP };
  if (room.left >= size.w) return { top: clampTop(rect.top), left: rect.left - GAP - size.w };
  // The element fills the screen: pin the card to the bottom-right corner, fully visible.
  return { top: clampTop(vh - size.h - MARGIN), left: clampLeft(vw - size.w - MARGIN) };
}

/** Guided product tour: spotlights elements marked with data-tour="…" one step at a time. */
export function TourProvider({ children }: { children: ReactNode }) {
  const { user, updatePreferences } = useAuth();
  const [run, setRun] = useState<{ key: string; steps: TourStep[] } | null>(null);
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);

  const done = (user?.preferences?.tours_done as string[] | undefined) ?? [];
  const current = useRef<{ key: string; steps: TourStep[] } | null>(null);
  const [available, setAvailable] = useState(false);
  const register = useCallback((key: string, steps: TourStep[]) => {
    current.current = { key, steps };
    setAvailable(true);
  }, []);
  const unregister = useCallback((key: string) => {
    if (current.current?.key !== key) return;
    current.current = null;
    setAvailable(false);
  }, []);

  const start = useCallback((key: string, steps: TourStep[], force = false) => {
    if (!force && done.includes(key)) return;
    const available = steps.filter((s) => document.querySelector(`[data-tour="${s.target}"]`));
    if (!available.length) return;
    setIndex(0);
    setRun({ key, steps: available });
  }, [done]);

  const finish = useCallback(() => {
    if (run && !done.includes(run.key)) updatePreferences({ tours_done: [...done, run.key] }).catch(() => {});
    setRun(null);
  }, [run, done, updatePreferences]);

  const replay = useCallback(() => {
    if (current.current) start(current.current.key, current.current.steps, true);
  }, [start]);

  const step = run?.steps[index];

  useLayoutEffect(() => {
    if (!step) return;
    const node = document.querySelector(`[data-tour="${step.target}"]`) as HTMLElement | null;
    if (!node) return;
    // Centre the element when it fits, so there is room for the card above or below it.
    const fits = node.getBoundingClientRect().height < window.innerHeight - 260;
    node.scrollIntoView({ block: fits ? "center" : "start", behavior: "smooth" });
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setRect(node.getBoundingClientRect()));
    };
    update();
    const timers = [150, 350, 700].map((ms) => setTimeout(update, ms)); // follow the smooth scroll
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true); // capture: inner scroll areas too
    return () => {
      timers.forEach(clearTimeout);
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [step]);

  // Measure the card so it is placed by its real size, not a guess.
  const cardRef = useRef<HTMLDivElement>(null);
  const [cardSize, setCardSize] = useState({ w: 340, h: 200 });
  useLayoutEffect(() => {
    const el = cardRef.current;
    if (!el) return;
    const measure = () => setCardSize((s) => (s.w === el.offsetWidth && s.h === el.offsetHeight ? s : { w: el.offsetWidth, h: el.offsetHeight }));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [run, index]);

  useEffect(() => {
    if (!run) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish();
      if (e.key === "ArrowRight") setIndex((i) => Math.min(run.steps.length - 1, i + 1));
      if (e.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run, finish]);

  const cardStyle = placeCard(rect, cardSize);

  return (
    <TourContext.Provider value={{ start, register, unregister, replay, active: !!run, available }}>
      {children}
      {run && step && (
        <>
          <div className="tour-shade" onClick={(e) => e.stopPropagation()} />
          {rect && <div className="tour-spot" style={{ top: rect.top - 6, left: rect.left - 6, width: rect.width + 12, height: rect.height + 12 }} />}
          <div ref={cardRef} className="tour-card" style={cardStyle} role="dialog" aria-label="Product guide">
            <div className="tour-body">
              <div className="subtle" style={{ marginBottom: 6 }}>Step {index + 1} of {run.steps.length}</div>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </div>
            <div className="foot">
              <div className="tour-dots">{run.steps.map((_, i) => <i key={i} className={i === index ? "on" : ""} />)}</div>
              <div className="spacer" />
              <button className="btn btn-ghost btn-sm" onClick={finish}>Skip guide</button>
              {index > 0 && <button className="btn btn-sm" onClick={() => setIndex(index - 1)}>Back</button>}
              {index < run.steps.length - 1 ? (
                <button className="btn btn-primary btn-sm" onClick={() => setIndex(index + 1)}>Next</button>
              ) : (
                <button className="btn btn-signal btn-sm" onClick={finish}>Finish</button>
              )}
            </div>
          </div>
        </>
      )}
    </TourContext.Provider>
  );
}
