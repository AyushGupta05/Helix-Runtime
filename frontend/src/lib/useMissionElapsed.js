import { useEffect, useMemo, useRef, useState } from "react";

import { getMissionElapsedSeconds } from "./format";

export function useMissionElapsedSeconds(mission) {
  const [now, setNow] = useState(Date.now());
  const anchorRef = useRef({
    snapshotKey: "",
    snapshotReceivedAt: Date.now()
  });

  const snapshotKey = [
    mission?.mission_id ?? "",
    mission?.run_state ?? "",
    mission?.updated_at ?? "",
    mission?.runtime_seconds ?? 0,
    mission?.latest_event_id ?? 0
  ].join("|");

  useEffect(() => {
    if (!mission) {
      return;
    }
    if (anchorRef.current.snapshotKey !== snapshotKey) {
      anchorRef.current = {
        snapshotKey,
        snapshotReceivedAt: Date.now()
      };
      setNow(Date.now());
    }
  }, [mission, snapshotKey]);

  useEffect(() => {
    if (!mission || !["running", "cancelling"].includes(mission.run_state)) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [mission, snapshotKey]);

  return useMemo(
    () =>
      getMissionElapsedSeconds(mission, {
        now,
        snapshotReceivedAt: anchorRef.current.snapshotReceivedAt
      }),
    [mission, now]
  );
}

export default useMissionElapsedSeconds;
