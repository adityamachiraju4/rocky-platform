import { useEffect, useRef, type RefObject } from "react";

type PresenceState = "idle" | "listening" | "transcribing" | "thinking" | "speaking" | "success" | "error";

/** Presentation only: reads the existing capture analyser without owning audio. */
export default function RockyPresence({ state, analyserRef }: {
  state: PresenceState;
  analyserRef: RefObject<AnalyserNode | null>;
}) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface || state !== "listening") return;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let samples = new Uint8Array(0);
    const update = () => {
      if (reducedMotion.matches) return;
      const analyser = analyserRef.current;
      if (analyser) {
        if (samples.length !== analyser.fftSize) samples = new Uint8Array(analyser.fftSize);
        analyser.getByteTimeDomainData(samples);
        const energy = Math.sqrt(samples.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / samples.length);
        surface.style.setProperty("--input-energy", String(Math.min(energy * 9, 1)));
      } else {
        surface.style.setProperty("--input-energy", "0");
      }
      frame = window.requestAnimationFrame(update);
    };
    const onMotionChange = () => {
      window.cancelAnimationFrame(frame);
      surface.style.setProperty("--input-energy", "0");
      if (!reducedMotion.matches) frame = window.requestAnimationFrame(update);
    };
    reducedMotion.addEventListener("change", onMotionChange);
    onMotionChange();
    return () => {
      reducedMotion.removeEventListener("change", onMotionChange);
      window.cancelAnimationFrame(frame);
      surface.style.removeProperty("--input-energy");
    };
  }, [state, analyserRef]);

  return <div className="cc-presence-stage" aria-hidden="true">
    <div ref={surfaceRef} className={`cc-presence cc-presence--${state}`} data-state={state}>
      <div className="cc-orbit cc-orbit-outer" />
      <div className="cc-orbit cc-orbit-inner" />
      <div className="cc-audio-ring" />
      <div className="cc-orb"><i /><i /><i /><span className="cc-orb-core" /></div>
      <div className="cc-completion-ring" />
    </div>
    <div className="cc-horizon" />
  </div>;
}
