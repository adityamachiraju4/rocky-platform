import rockyPresenceAsset from "./assets/rocky-presence.png";

export type RockyPresenceState = "ready" | "listening" | "thinking" | "speaking";

export default function RockyPresence({ state }: { state: RockyPresenceState }) {
  return (
    <div className={`rocky-presence rocky-presence-${state}`} aria-hidden="true">
      <div className="rocky-presence-atmosphere" />
      <div className="rocky-presence-ripple" />
      <div className="rocky-presence-sweep" />
      <div className="rocky-presence-speech" />
      <img className="rocky-presence-image" src={rockyPresenceAsset} alt="" draggable="false" />
    </div>
  );
}
