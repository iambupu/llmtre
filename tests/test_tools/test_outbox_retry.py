from tools.sqlite_db.db_updater import DBUpdater


def test_outbox_failed_event_moves_to_dead_letter(tmp_path):
    db_path = tmp_path / "tre_state.db"
    updater = DBUpdater(str(db_path))
    event_id = updater.enqueue_outer_event("turn_ended", {"turn_id": 1}, "seed error")

    updater.mark_outer_event_failed(
        event_id=event_id,
        error="retry-1",
        max_attempts=2,
        base_backoff_seconds=1,
    )
    updater.mark_outer_event_failed(
        event_id=event_id,
        error="retry-2",
        max_attempts=2,
        base_backoff_seconds=1,
    )

    with updater._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT status, attempts FROM outer_event_outbox WHERE id = ?",
            (event_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "dead_letter"
    assert int(row[1]) >= 2


def test_outbox_reserve_only_returns_retryable_events(tmp_path):
    db_path = tmp_path / "tre_state.db"
    updater = DBUpdater(str(db_path))
    event_id = updater.enqueue_outer_event(
        "state_changed",
        {"entity_id": "player_01"},
        "seed error",
    )
    rows = updater.reserve_pending_outer_events(limit=10)
    assert rows
    assert rows[0]["id"] == event_id

    # 被 reserve 之后状态变为 processing，不应重复返回
    rows_again = updater.reserve_pending_outer_events(limit=10)
    assert not rows_again


def test_outbox_reclaim_stuck_processing_event(tmp_path):
    db_path = tmp_path / "tre_state.db"
    updater = DBUpdater(str(db_path))
    event_id = updater.enqueue_outer_event("turn_ended", {"turn_id": 1}, "seed error")
    rows = updater.reserve_pending_outer_events(limit=10)
    assert rows and rows[0]["id"] == event_id

    with updater._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            """
            UPDATE outer_event_outbox
            SET updated_at = datetime('now', '-120 seconds')
            WHERE id = ?
            """,
            (event_id,),
        )
        conn.commit()

    reclaimed = updater.reclaim_stuck_processing_outer_events(timeout_seconds=30)
    assert reclaimed == 1

    rows_replayed = updater.reserve_pending_outer_events(limit=10, processing_timeout_seconds=30)
    assert rows_replayed
    assert rows_replayed[0]["id"] == event_id
