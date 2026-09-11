"""Durable local reservation ledger; it neither authorizes nor sends API requests."""
import sqlite3
from pathlib import Path

TEST_LIMIT_MICRO_USD = 250000
ATTEMPT_RESERVATION_MICRO_USD = 10000


def reserve_attempt(path, attempt_id):
    """Reserve one cent before an attempt, including timeouts. Never auto-refund.

    Caller must independently verify owner approval and trial-credit eligibility.
    This controls this application's attempts, not provider/account-wide billing.
    An existing ID is refused so an uncertain request cannot be sent twice.
    """
    if not isinstance(attempt_id, str) or not 1 <= len(attempt_id) <= 128:
        raise ValueError('invalid attempt ID')
    connection = sqlite3.connect(str(Path(path)), timeout=5)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, reserved INTEGER NOT NULL CHECK(reserved = 10000), status TEXT NOT NULL)')
        if connection.execute('SELECT 1 FROM attempts WHERE id = ?', (attempt_id,)).fetchone():
            raise ValueError('attempt already reserved; do not resend')
        used = connection.execute('SELECT COALESCE(SUM(reserved),0) FROM attempts').fetchone()[0]
        if used + ATTEMPT_RESERVATION_MICRO_USD > TEST_LIMIT_MICRO_USD:
            raise ValueError('test allowance exhausted')
        connection.execute('INSERT INTO attempts VALUES (?, ?, ?)',
                           (attempt_id, ATTEMPT_RESERVATION_MICRO_USD, 'reserved'))
        connection.commit()
        return {'attempt_id': attempt_id, 'reserved_micro_usd': ATTEMPT_RESERVATION_MICRO_USD,
                'remaining_micro_usd': TEST_LIMIT_MICRO_USD - used - ATTEMPT_RESERVATION_MICRO_USD}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_outcome(path, attempt_id, status):
    if status not in ('completed', 'failed', 'uncertain'):
        raise ValueError('invalid outcome')
    connection = sqlite3.connect(str(Path(path)), timeout=5)
    try:
        cursor = connection.execute('UPDATE attempts SET status = ? WHERE id = ? AND status = ?',
                                    (status, attempt_id, 'reserved'))
        if cursor.rowcount != 1:
            raise ValueError('missing or already finalized attempt')
        connection.commit()
    finally:
        connection.close()
