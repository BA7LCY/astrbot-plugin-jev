"""有限保留的 SQLite 审计记录。"""

import json
import sqlite3
import threading
from pathlib import Path


class History:
    def __init__(self, path: Path, limit: int):
        self.path = path
        self.limit = limit
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS decisions "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"
            )

    def append(self, record: dict) -> None:
        """保存记录并限制总条数。

        Args:
            record: 不含凭据的完整判断快照。
        """
        payload = json.dumps(record, ensure_ascii=False, allow_nan=False)
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO decisions(payload) VALUES (?)", (payload,))
            db.execute(
                "DELETE FROM decisions WHERE id NOT IN "
                "(SELECT id FROM decisions ORDER BY id DESC LIMIT ?)",
                (self.limit,),
            )

    def recent(self, before: int = 0, limit: int = 30) -> list[dict]:
        """读取倒序分页记录。

        Args:
            before: 上一页最后一个 ID，零代表首页。
            limit: 每页数量，上限 100。

        Returns:
            包含数据库 ID 的历史判断。
        """
        with self.lock, sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT id, payload FROM decisions WHERE (? = 0 OR id < ?) "
                "ORDER BY id DESC LIMIT ?",
                (before, before, min(max(limit, 1), 100)),
            ).fetchall()
        return [{"id": row[0], **json.loads(row[1])} for row in rows]
