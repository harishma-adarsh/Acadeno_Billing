import sqlite3
import os
from datetime import datetime

DATABASE = "billing.db"

def insert_receipt():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    # Student details
    email = "harishma.receipt@example.com"
    phone = "9000000023"
    name = "Harishma Adarsh"
    address = "Kochi, Kerala"
    course = "ML/AI"
    duration = "45 Days"
    joining_date = "2026-02-02"
    validity = "2026-03-23"
    fee = 15000
    discount = 500
    paid_amount = 14500
    
    # Check if student exists
    cur.execute("SELECT id FROM students WHERE email = ?", (email,))
    student = cur.fetchone()
    
    if student:
        student_id = student[0]
        cur.execute("""
            UPDATE students SET 
            name=?, address=?, phone=?, course=?, duration=?, joining_date=?, validity=?, fee=?, discount=?
            WHERE id=?
        """, (name, address, phone, course, duration, joining_date, validity, fee, discount, student_id))
    else:
        cur.execute("""
            INSERT INTO students (email, phone, name, address, course, duration, joining_date, validity, fee, discount, total_installments, salutation, approved_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (email, phone, name, address, course, duration, joining_date, validity, fee, discount, 1, "Ms.", "1"))
        student_id = cur.lastrowid

    # Get Next Invoice Number
    invoice_no = "ACT-025-R-002" # Based on invoice.txt being 2
    
    # Payment record
    payment_date = "13-03-2026" # Today's date in DD-MM-YYYY as per app.py logic
    installment_text = "1"
    
    cur.execute("""
        INSERT INTO payments (student_id, invoice_no, amount, payment_date, installment_text)
        VALUES (?, ?, ?, ?, ?)
    """, (student_id, invoice_no, paid_amount, payment_date, installment_text))

    # Update invoice.txt
    if os.path.exists("invoice.txt"):
        with open("invoice.txt", "w") as f:
            f.write("3")

    conn.commit()
    conn.close()
    print(f"Successfully created receipt {invoice_no} for {name}")

if __name__ == "__main__":
    insert_receipt()
