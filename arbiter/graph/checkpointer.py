from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from threading import RLock
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import WRITES_IDX_MAP

from arbiter.core.contracts import utc_now


def _version_key(value: str | int | float) -> str:
    return f"{type(value).__name__}:{value}"


class MissionSqliteCheckpointer(BaseCheckpointSaver[str]):
    def __init__(self, db_path: str) -> None:
        super().__init__()
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    parent_checkpoint_id TEXT,
                    checkpoint_type TEXT NOT NULL,
                    checkpoint_blob BLOB NOT NULL,
                    metadata_type TEXT NOT NULL,
                    metadata_blob BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                );
                CREATE INDEX IF NOT EXISTS idx_langgraph_checkpoints_thread
                    ON langgraph_checkpoints(thread_id, checkpoint_ns, created_at DESC);

                CREATE TABLE IF NOT EXISTS langgraph_blobs (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL,
                    version_key TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    value_blob BLOB NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, channel, version_key)
                );

                CREATE TABLE IF NOT EXISTS langgraph_writes (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    write_idx INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    value_blob BLOB NOT NULL,
                    task_path TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx)
                );
                """
            )
            connection.commit()

    @staticmethod
    def _checkpoint_ns(config: RunnableConfig) -> str:
        return config["configurable"].get("checkpoint_ns", "")

    def _load_channel_values(
        self,
        connection: sqlite3.Connection,
        *,
        thread_id: str,
        checkpoint_ns: str,
        channel_versions: ChannelVersions,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for channel, version in channel_versions.items():
            row = connection.execute(
                """
                SELECT value_type, value_blob
                FROM langgraph_blobs
                WHERE thread_id = ? AND checkpoint_ns = ? AND channel = ? AND version_key = ?
                """,
                (thread_id, checkpoint_ns, channel, _version_key(version)),
            ).fetchone()
            if row is None or row["value_type"] == "empty":
                continue
            values[channel] = self.serde.loads_typed((row["value_type"], row["value_blob"]))
        return values

    def _row_to_tuple(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed((row["checkpoint_type"], row["checkpoint_blob"]))
        metadata = self.serde.loads_typed((row["metadata_type"], row["metadata_blob"]))
        pending_rows = connection.execute(
            """
            SELECT task_id, channel, value_type, value_blob
            FROM langgraph_writes
            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
            ORDER BY task_id ASC, write_idx ASC
            """,
            (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"]),
        ).fetchall()
        pending_writes = [
            (
                write["task_id"],
                write["channel"],
                self.serde.loads_typed((write["value_type"], write["value_blob"])),
            )
            for write in pending_rows
        ]
        checkpoint = {
            **checkpoint,
            "channel_values": self._load_channel_values(
                connection,
                thread_id=row["thread_id"],
                checkpoint_ns=row["checkpoint_ns"],
                channel_versions=checkpoint["channel_versions"],
            ),
        }
        parent_config = None
        if row["parent_checkpoint_id"]:
            parent_config = {
                "configurable": {
                    "thread_id": row["thread_id"],
                    "checkpoint_ns": row["checkpoint_ns"],
                    "checkpoint_id": row["parent_checkpoint_id"],
                }
            }
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row["thread_id"],
                    "checkpoint_ns": row["checkpoint_ns"],
                    "checkpoint_id": row["checkpoint_id"],
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = self._checkpoint_ns(config)
        checkpoint_id = get_checkpoint_id(config)
        with self._lock, self._connect() as connection:
            if checkpoint_id:
                row = connection.execute(
                    """
                    SELECT *
                    FROM langgraph_checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT *
                    FROM langgraph_checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ?
                    ORDER BY created_at DESC, checkpoint_id DESC
                    LIMIT 1
                    """,
                    (thread_id, checkpoint_ns),
                ).fetchone()
            return self._row_to_tuple(connection, row) if row is not None else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        clauses = []
        params: list[Any] = []
        if config:
            clauses.append("thread_id = ?")
            params.append(config["configurable"]["thread_id"])
            clauses.append("checkpoint_ns = ?")
            params.append(self._checkpoint_ns(config))
        if before and get_checkpoint_id(before):
            clauses.append("checkpoint_id <> ?")
            params.append(get_checkpoint_id(before))
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            "SELECT * FROM langgraph_checkpoints "
            f"{where_clause} ORDER BY created_at DESC, checkpoint_id DESC"
        )
        if limit:
            query += f" LIMIT {int(limit)}"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
            for row in rows:
                tuple_ = self._row_to_tuple(connection, row)
                if filter and any(tuple_.metadata.get(key) != value for key, value in filter.items()):
                    continue
                yield tuple_

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = self._checkpoint_ns(config)
        checkpoint_copy = checkpoint.copy()
        channel_values = checkpoint_copy.pop("channel_values", {})
        parent_checkpoint_id = get_checkpoint_id(config)
        serialized_checkpoint = self.serde.dumps_typed(checkpoint_copy)
        serialized_metadata = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        with self._lock, self._connect() as connection:
            for channel, version in new_versions.items():
                value = channel_values[channel] if channel in channel_values else None
                value_type, value_blob = (
                    self.serde.dumps_typed(value) if channel in channel_values else ("empty", b"")
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO langgraph_blobs (
                        thread_id, checkpoint_ns, channel, version_key, value_type, value_blob
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        checkpoint_ns,
                        channel,
                        _version_key(version),
                        value_type,
                        sqlite3.Binary(value_blob),
                    ),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO langgraph_checkpoints (
                    thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                    checkpoint_type, checkpoint_blob, metadata_type, metadata_blob, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint["id"],
                    parent_checkpoint_id,
                    serialized_checkpoint[0],
                    sqlite3.Binary(serialized_checkpoint[1]),
                    serialized_metadata[0],
                    sqlite3.Binary(serialized_metadata[1]),
                    utc_now().isoformat(),
                ),
            )
            connection.commit()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = self._checkpoint_ns(config)
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with self._lock, self._connect() as connection:
            for index, (channel, value) in enumerate(writes):
                write_index = WRITES_IDX_MAP.get(channel, index)
                value_type, value_blob = self.serde.dumps_typed(value)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO langgraph_writes (
                        thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx, channel,
                        value_type, value_blob, task_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        task_id,
                        write_index,
                        channel,
                        value_type,
                        sqlite3.Binary(value_blob),
                        task_path,
                    ),
                )
            connection.commit()

    def delete_thread(self, thread_id: str) -> None:
        with self._lock, self._connect() as connection:
            for table in ("langgraph_writes", "langgraph_blobs", "langgraph_checkpoints"):
                connection.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))
            connection.commit()

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        if not run_ids:
            return
        placeholders = ",".join("?" for _ in run_ids)
        with self._lock, self._connect() as connection:
            for table in ("langgraph_writes", "langgraph_blobs", "langgraph_checkpoints"):
                connection.execute(f"DELETE FROM {table} WHERE thread_id IN ({placeholders})", tuple(run_ids))
            connection.commit()

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO langgraph_checkpoints
                SELECT ?, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                       checkpoint_type, checkpoint_blob, metadata_type, metadata_blob, created_at
                FROM langgraph_checkpoints
                WHERE thread_id = ?
                """,
                (target_thread_id, source_thread_id),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO langgraph_blobs
                SELECT ?, checkpoint_ns, channel, version_key, value_type, value_blob
                FROM langgraph_blobs
                WHERE thread_id = ?
                """,
                (target_thread_id, source_thread_id),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO langgraph_writes
                SELECT ?, checkpoint_ns, checkpoint_id, task_id, write_idx, channel, value_type, value_blob, task_path
                FROM langgraph_writes
                WHERE thread_id = ?
                """,
                (target_thread_id, source_thread_id),
            )
            connection.commit()

    def prune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        if not thread_ids:
            return
        if strategy == "delete":
            self.delete_for_runs(thread_ids)
            return
        with self._lock, self._connect() as connection:
            for thread_id in thread_ids:
                rows = connection.execute(
                    """
                    SELECT checkpoint_ns, checkpoint_id
                    FROM langgraph_checkpoints
                    WHERE thread_id = ?
                    ORDER BY created_at DESC, checkpoint_id DESC
                    """,
                    (thread_id,),
                ).fetchall()
                keep: set[tuple[str, str]] = set()
                for row in rows:
                    checkpoint_ns = row["checkpoint_ns"]
                    if checkpoint_ns in {item[0] for item in keep}:
                        continue
                    keep.add((checkpoint_ns, row["checkpoint_id"]))
                for row in rows:
                    key = (row["checkpoint_ns"], row["checkpoint_id"])
                    if key in keep:
                        continue
                    connection.execute(
                        """
                        DELETE FROM langgraph_writes
                        WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                        """,
                        (thread_id, row["checkpoint_ns"], row["checkpoint_id"]),
                    )
                    connection.execute(
                        """
                        DELETE FROM langgraph_checkpoints
                        WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                        """,
                        (thread_id, row["checkpoint_ns"], row["checkpoint_id"]),
                    )
            connection.commit()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        self.delete_for_runs(run_ids)

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        self.copy_thread(source_thread_id, target_thread_id)

    async def aprune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        self.prune(thread_ids, strategy=strategy)
