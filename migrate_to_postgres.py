"""
migrate_to_postgres.py
======================
One-time script to copy all existing data from the local SQLite billing.db
into the shared Supabase PostgreSQL database.

HOW TO RUN (on the machine that has the most up-to-date billing.db):

  1. Make sure DATABASE_URL is set (either in .env or as an environment variable)
  2. Run:  python migrate_to_postgres.py

This script is safe to re-run — it uses ON CONFLICT DO UPDATE / DO NOTHING.
"""

import os
import sqlite3
import psycopg2
import psycopg2.extras

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SQLITE_DB = "billing.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL is not set. Add it to your .env file or environment.")
    exit(1)

if not os.path.exists(SQLITE_DB):
    print(f"ERROR: {SQLITE_DB} not found in the current directory.")
    exit(1)

print(f"Reading from SQLite: {SQLITE_DB}")
sqlite_conn = sqlite3.connect(SQLITE_DB)
sqlite_conn.row_factory = sqlite3.Row

print(f"Connecting to PostgreSQL...")
pg_conn = psycopg2.connect(DATABASE_URL)

try:
    # ---------- Read all SQLite data ----------
    students = sqlite_conn.execute("SELECT * FROM students").fetchall()
    payments = sqlite_conn.execute("SELECT * FROM payments ORDER BY id ASC").fetchall()
    print(f"Found {len(students)} student(s) and {len(payments)} payment(s) in SQLite.\n")

    with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

        # ---------- Migrate students ----------
        student_id_map = {}   # old SQLite id → new PostgreSQL id

        for s in students:
            s = dict(s)
            try:
                cur.execute('''
                    INSERT INTO students
                        (name, email, phone, address, alt_phone, course, duration,
                         joining_date, validity, fee, discount, approved_text,
                         total_installments, salutation)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (email) DO UPDATE SET
                        name             = EXCLUDED.name,
                        phone            = EXCLUDED.phone,
                        address          = EXCLUDED.address,
                        alt_phone        = EXCLUDED.alt_phone,
                        course           = EXCLUDED.course,
                        duration         = EXCLUDED.duration,
                        joining_date     = EXCLUDED.joining_date,
                        validity         = EXCLUDED.validity,
                        fee              = EXCLUDED.fee,
                        discount         = EXCLUDED.discount,
                        approved_text    = EXCLUDED.approved_text,
                        total_installments = EXCLUDED.total_installments,
                        salutation       = EXCLUDED.salutation
                    RETURNING id
                ''', (
                    s.get('name'), s.get('email'), s.get('phone'),
                    s.get('address'), s.get('alt_phone'), s.get('course'),
                    s.get('duration'), s.get('joining_date'), s.get('validity'),
                    s.get('fee'), s.get('discount'), s.get('approved_text'),
                    s.get('total_installments'), s.get('salutation')
                ))
                new_id = cur.fetchone()['id']
                student_id_map[s['id']] = new_id
                print(f"  [SUCCESS] Student: {s['name']}  (SQLite id={s['id']} -> PG id={new_id})")
            except Exception as e:
                print(f"  [ERROR] ERROR migrating student '{s.get('name')}': {e}")

        # ---------- Migrate payments ----------
        print()
        for p in payments:
            p = dict(p)
            new_student_id = student_id_map.get(p['student_id'])
            if new_student_id is None:
                print(f"  [WARN] SKIP payment {p.get('invoice_no')} - student id={p['student_id']} not migrated")
                continue
            try:
                cur.execute('''
                    INSERT INTO payments (student_id, invoice_no, amount, payment_date, installment_text)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                ''', (
                    new_student_id, p.get('invoice_no'), p.get('amount'),
                    p.get('payment_date'), p.get('installment_text', '')
                ))
                print(f"  [SUCCESS] Payment: {p.get('invoice_no')}  Rs {p.get('amount')}")
            except Exception as e:
                print(f"  [ERROR] ERROR migrating payment {p.get('invoice_no')}: {e}")

        # ---------- Set invoice counter to max existing + 1 ----------
        max_num = 0
        for p in payments:
            p = dict(p)
            inv = p.get('invoice_no') or ''
            try:
                num = int(inv.split('-')[-1])
                max_num = max(max_num, num)
            except Exception:
                pass
        next_counter = max_num + 1
        cur.execute("UPDATE invoice_counter SET counter = %s WHERE id = 1", (next_counter,))
        print(f"\n  [INFO] Invoice counter set to {next_counter}  "
              f"(next invoice: ACT-025-R-{str(next_counter).zfill(3)})")

    pg_conn.commit()
    print("\n[SUCCESS] Migration complete! All data is now in Supabase PostgreSQL.")

except Exception as e:
    pg_conn.rollback()
    print(f"\n[ERROR] Migration failed: {e}")
    raise

finally:
    sqlite_conn.close()
    pg_conn.close()
