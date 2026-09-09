import os
import sqlite3
import sys

try:
    import psycopg
except ImportError:
    print("Missing psycopg. Run: pip install 'psycopg[binary]>=3.2,<4'")
    sys.exit(1)

SQLITE_DB = os.environ.get("SQLITE_DB", "indian_physios.db")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL is not set.")
    print("Set it in Terminal first, then run this script again.")
    sys.exit(1)

TABLES = [
    ("users", ["id", "name", "email", "password_hash", "phone", "qualification", "specialization", "state", "city", "pincode", "registration_no", "verified_email", "verified_physio", "photo", "photo_data", "photo_mime", "bio", "clinic", "experience", "education", "website", "created_at"]),
    ("verification_tokens", ["id", "user_id", "token", "expires_at"]),
    ("connections", ["id", "requester_id", "receiver_id", "status", "created_at"]),
    ("posts", ["id", "user_id", "title", "body", "created_at"]),
    ("jobs", ["id", "user_id", "title", "organization", "city", "description", "created_at"]),
    ("home_visits", ["id", "user_id", "location", "diagnosis", "physio_need", "details", "pincode", "created_at"]),
    ("notifications", ["id", "user_id", "home_visit_id", "title", "body", "created_at", "read_at"]),
    ("events", ["id", "user_id", "title", "location", "event_date", "description", "created_at"]),
    ("messages", ["id", "sender_id", "receiver_id", "body", "created_at", "read_at"]),
]

CREATE_SQL = {
    "users": '''CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        phone TEXT DEFAULT '', qualification TEXT DEFAULT '', specialization TEXT DEFAULT '',
        state TEXT DEFAULT '', city TEXT DEFAULT '', pincode TEXT DEFAULT '', registration_no TEXT DEFAULT '',
        verified_email INTEGER DEFAULT 0, verified_physio INTEGER DEFAULT 0,
        photo TEXT DEFAULT '', photo_data BYTEA, photo_mime TEXT DEFAULT '',
        bio TEXT DEFAULT '', clinic TEXT DEFAULT '', experience TEXT DEFAULT '', education TEXT DEFAULT '', website TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    "verification_tokens": '''CREATE TABLE IF NOT EXISTS verification_tokens (
        id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, token TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL
    )''',
    "connections": '''CREATE TABLE IF NOT EXISTS connections (
        id BIGSERIAL PRIMARY KEY, requester_id BIGINT NOT NULL, receiver_id BIGINT NOT NULL, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    "posts": '''CREATE TABLE IF NOT EXISTS posts (
        id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    "jobs": '''CREATE TABLE IF NOT EXISTS jobs (
        id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, title TEXT NOT NULL, organization TEXT DEFAULT '', city TEXT DEFAULT '', description TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    "home_visits": '''CREATE TABLE IF NOT EXISTS home_visits (
        id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, location TEXT NOT NULL, diagnosis TEXT DEFAULT '', physio_need TEXT NOT NULL, details TEXT DEFAULT '', pincode TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    "notifications": '''CREATE TABLE IF NOT EXISTS notifications (
        id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, home_visit_id BIGINT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, read_at TEXT
    )''',
    "events": '''CREATE TABLE IF NOT EXISTS events (
        id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, title TEXT NOT NULL, location TEXT DEFAULT '', event_date TEXT NOT NULL, description TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    "messages": '''CREATE TABLE IF NOT EXISTS messages (
        id BIGSERIAL PRIMARY KEY, sender_id BIGINT NOT NULL, receiver_id BIGINT NOT NULL, body TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, read_at TEXT
    )''',
}


def main():
    if not os.path.exists(SQLITE_DB):
        print(f"ERROR: SQLite database not found: {SQLITE_DB}")
        print("Run this script from the Indian Physios V8 Production folder.")
        sys.exit(1)

    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.row_factory = sqlite3.Row

    print(f"Reading local database: {SQLITE_DB}")
    print("Connecting to Supabase PostgreSQL...")

    try:
        pg_conn = psycopg.connect(DATABASE_URL)
    except Exception as exc:
        print("ERROR: Could not connect to Supabase PostgreSQL.")
        print(str(exc).splitlines()[0])
        sys.exit(1)

    try:
        with pg_conn.cursor() as cur:
            for table, _ in TABLES:
                cur.execute(CREATE_SQL[table])
        pg_conn.commit()

        # Ensure this really is the expected empty/new database before copying.
        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM users")
            existing_users = cur.fetchone()[0]
        if existing_users:
            raise RuntimeError("Supabase users table is not empty. Migration stopped to avoid duplicate data.")

        for table, columns in TABLES:
            sqlite_rows = sqlite_conn.execute(
                f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"
            ).fetchall()
            if not sqlite_rows:
                print(f"{table}: 0 rows")
                continue

            col_sql = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))
            insert_sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"

            with pg_conn.cursor() as cur:
                for row in sqlite_rows:
                    values = []
                    for col in columns:
                        value = row[col]
                        if col in ("verified_email", "verified_physio") and value is not None:
                            value = int(value)
                        values.append(value)
                    cur.execute(insert_sql, values)
            pg_conn.commit()
            print(f"{table}: {len(sqlite_rows)} rows copied")

        # Advance PostgreSQL sequences so newly-created records continue after
        # the imported IDs instead of trying to reuse IDs that already exist.
        with pg_conn.cursor() as cur:
            for table, _ in TABLES:
                cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
                max_id = cur.fetchone()[0]
                if max_id:
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), %s, true)",
                        (max_id,),
                    )
        pg_conn.commit()

        print("\nSUCCESS: Local Indian Physios data has been copied to Supabase PostgreSQL.")
        print("Your local SQLite database was not changed.")

    except Exception as exc:
        pg_conn.rollback()
        print("\nERROR: Migration failed. Supabase changes were rolled back where possible.")
        print(str(exc))
        sys.exit(1)
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
