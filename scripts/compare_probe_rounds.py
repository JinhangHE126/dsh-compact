"""对比 probe_captures 里最近两轮请求的 messages，辅助 Gate B。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / "probe_captures"


def main() -> int:
    files = sorted(CAP.glob("req_*.bin"))
    if len(files) < 2:
        print(f"Need >=2 captures in {CAP}, found {len(files)}")
        return 1
    a, b = files[-2], files[-1]
    for label, f in (("round1", a), ("round2", b)):
        d = json.loads(f.read_bytes())
        msgs = d.get("messages") or []
        print(f"{label} {f.name}")
        print(f"  messages={len(msgs)} roles={[m.get('role') for m in msgs]}")
        if msgs:
            c = msgs[-1].get("content")
            snippet = c[:100] if isinstance(c, str) else str(c)[:100]
            print(f"  last: role={msgs[-1].get('role')} {snippet!r}")
    n1 = len(json.loads(a.read_bytes()).get("messages") or [])
    n2 = len(json.loads(b.read_bytes()).get("messages") or [])
    print()
    if n2 > n1:
        print(f"Gate B likely PASS: messages grew {n1} -> {n2}")
        return 0
    print(f"Gate B likely FAIL: messages did not grow ({n1} -> {n2})")
    return 2


if __name__ == "__main__":
    sys.exit(main())
