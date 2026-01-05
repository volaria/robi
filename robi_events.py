import time
from typing import Any, Dict

def make_event(
    type: str,
    source: str,
    payload: Dict[str, Any] | None = None,
    ts: float | None = None,
) -> Dict[str, Any]:
    return {
        "type": type,
        "source": source,
        "payload": payload or {},
        "ts": ts if ts is not None else time.time(),
    }
