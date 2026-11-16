"""Append only run log. Each line carries the hash of the previous line, so any edit is detectable."""
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(os.getenv("GRANT_APP_LOG", Path(__file__).resolve().parent.parent / "runs" / "run_log.jsonl"))


def _digest(entry: dict) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class RunLog:
    def __init__(self, path=None):
        self.path = Path(path or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = threading.Lock()
        rows = self.rows()
        self._seq = len(rows)
        self._prev = rows[-1]["hash"] if rows else "GENESIS"

    def append(self, app_id, agent, event, detail):
        # The file is only ever opened in append mode. There is no update or delete method.
        with self._lock:
            entry = {
                "seq": self._seq + 1,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "app_id": app_id,
                "agent": agent,
                "event": event,
                "detail": detail,
                "prev": self._prev,
            }
            entry["hash"] = _digest(entry)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            self._seq += 1
            self._prev = entry["hash"]
            return entry

    def rows(self):
        text = self.path.read_text(encoding="utf-8").strip()
        return [json.loads(line) for line in text.splitlines()] if text else []

    def verify(self):
        """Recompute the chain. Returns (ok, entries_checked, first_bad_seq)."""
        prev = "GENESIS"
        for row in self.rows():
            body = {k: v for k, v in row.items() if k != "hash"}
            if row["prev"] != prev or _digest(body) != row["hash"]:
                return False, row["seq"], row["seq"]
            prev = row["hash"]
        return True, self._seq, None
