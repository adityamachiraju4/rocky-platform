import { useEffect, useState } from "react";
import { useRegisterSW } from "virtual:pwa-register/react";

export default function PwaUpdate() {
  const [voiceActive, setVoiceActive] = useState(
    () => document.documentElement.dataset.voiceActive === "true",
  );
  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW();

  useEffect(() => {
    const onVoiceActivity = (event: Event) => {
      setVoiceActive((event as CustomEvent<boolean>).detail);
    };
    window.addEventListener("rocky:voice-activity", onVoiceActivity);
    return () => window.removeEventListener("rocky:voice-activity", onVoiceActivity);
  }, []);

  if (!needRefresh) return null;

  return (
    <div className="pwa-update" role="status">
      <span>{voiceActive ? "Rocky will update after this turn." : "Rocky has an update ready."}</span>
      <button type="button" disabled={voiceActive} onClick={() => void updateServiceWorker(true)}>Update</button>
      <button className="pwa-update-dismiss" type="button" aria-label="Dismiss update" onClick={() => setNeedRefresh(false)}>×</button>
    </div>
  );
}
