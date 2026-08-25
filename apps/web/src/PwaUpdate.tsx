import { useRegisterSW } from "virtual:pwa-register/react";

export default function PwaUpdate() {
  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW();

  if (!needRefresh) return null;

  return (
    <div className="pwa-update" role="status">
      <span>Rocky has an update ready.</span>
      <button type="button" onClick={() => void updateServiceWorker(true)}>Update</button>
      <button className="pwa-update-dismiss" type="button" aria-label="Dismiss update" onClick={() => setNeedRefresh(false)}>×</button>
    </div>
  );
}
