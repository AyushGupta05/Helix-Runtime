export default function MissionLiveView({ children }) {
  return (
    <div className="workspace-view workspace-live">
      <div className="live-market-stack">{children}</div>
    </div>
  );
}
