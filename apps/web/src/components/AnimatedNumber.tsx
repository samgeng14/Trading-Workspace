import { useEffect, useRef, useState } from "react";

const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

/** Ease-out cubic: fast at first, settling on the value. */
const ease = (t: number) => 1 - (1 - t) ** 3;

/**
 * A headline figure that counts to its value and flashes green or red when it ticks.
 * The first render counts up from just below the value; later changes tween from the
 * previous value. With reduced motion the value is set at once and nothing flashes.
 */
export function AnimatedNumber({
  value,
  format,
  duration = 900,
  className = "",
}: {
  value: number;
  format: (v: number) => string;
  duration?: number;
  className?: string;
}) {
  const [shown, setShown] = useState(() => (reducedMotion() ? value : value * 0.96));
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const from = useRef(shown);
  const first = useRef(true);

  useEffect(() => {
    if (reducedMotion()) {
      setShown(value);
      return;
    }
    const start = from.current;
    if (!first.current && value !== start) setFlash(value > start ? "up" : "down");
    const span = first.current ? duration * 1.3 : duration * 0.7;
    first.current = false;
    let raf = 0;
    const t0 = performance.now();
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / span);
      const v = start + (value - start) * ease(k);
      from.current = v;
      setShown(k === 1 ? value : v);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    const off = setTimeout(() => setFlash(null), 900);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(off);
    };
  }, [value, duration]);

  return (
    <span className={`num anim-num ${className}`} data-flash={flash ?? undefined}>
      {format(shown)}
    </span>
  );
}
