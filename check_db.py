import sqlite3
DATABASE = "billing.db"
conn = sqlite3.connect(DATABASE)
cur = conn.cursor()
print("Students count:", cur.execute("SELECT COUNT(*) FROM students").fetchone()[0])
print("Payments count:", cur.execute("SELECT COUNT(*) FROM payments").fetchone()[0])
print("\n--- Students ---")
students = cur.execute("SELECT * FROM students").fetchall()
for s in students:
    print(s)
print("\n--- Payments ---")
payments = cur.execute("SELECT * FROM payments").fetchall()
for p in payments:
    print(p)
conn.close()
