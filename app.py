import os
import json
import base64
from flask import Flask, render_template, request
import psycopg2
import psycopg2.extras

# Load .env file for local development (ignored in production)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_conn():
    """Open a new PostgreSQL connection using the DATABASE_URL env variable."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Set it to your Supabase connection string."
        )
    return psycopg2.connect(DATABASE_URL)


# ---------------------------------------------------------------------------
# Financial year helpers
# ---------------------------------------------------------------------------
def get_financial_year_code(date_str=None):
    """
    Returns the 3-digit year code for the financial year that contains the
    given date.  Indian financial year runs April 1 → March 31.

    Examples:
        2025-08-13  →  '025'   (FY 2025-26, prefix ACT-025-R-)
        2026-01-15  →  '025'   (still FY 2025-26)
        2026-04-01  →  '026'   (FY 2026-27, prefix ACT-026-R-)
    """
    from datetime import datetime
    if date_str:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            dt = datetime.now()
    else:
        dt = datetime.now()

    # FY starts in April; if month < April the FY started last calendar year
    fy_start_year = dt.year if dt.month >= 4 else dt.year - 1
    return str(fy_start_year)[-3:]   # e.g. 2025 → '025', 2026 → '026'


# ---------------------------------------------------------------------------
# Invoice counter  (per financial year, shared across all machines via DB)
# ---------------------------------------------------------------------------
def get_next_invoice_number(invoice_date=None, increment=True):
    """
    Returns the next invoice number string for the financial year of
    invoice_date, e.g. 'ACT-025-R-007' or 'ACT-026-R-001'.

    Each financial year has its own counter that starts at 001 and is
    stored atomically in the invoice_counter table — safe for concurrent
    use across multiple machines.
    """
    fy_code = get_financial_year_code(invoice_date)
    prefix = f"ACT-{fy_code}-R-"

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if increment:
                # UPSERT: first invoice for this FY inserts counter=2 and
                # returns 2-1=1; subsequent ones increment and return old value.
                cur.execute(
                    """
                    INSERT INTO invoice_counter (fy_code, counter)
                    VALUES (%s, 2)
                    ON CONFLICT (fy_code)
                    DO UPDATE SET counter = invoice_counter.counter + 1
                    RETURNING counter - 1
                    """,
                    (fy_code,)
                )
                row = cur.fetchone()
                number = row[0] if row else 1
            else:
                cur.execute(
                    "SELECT counter FROM invoice_counter WHERE fy_code = %s",
                    (fy_code,)
                )
                row = cur.fetchone()
                number = row[0] if row else 1
        conn.commit()
    finally:
        conn.close()
    return prefix + str(number).zfill(3)


# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------
def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # --- invoice counter table (one row per financial year) ---
            # Migrate old single-row schema to new fy_code-based schema
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'invoice_counter' AND column_name = 'id'
            """)
            if cur.fetchone():
                # Old schema exists — read current counter, drop table, recreate
                cur.execute("SELECT counter FROM invoice_counter WHERE id = 1")
                old_row = cur.fetchone()
                old_counter = old_row[0] if old_row else 1
                cur.execute("DROP TABLE invoice_counter")
                cur.execute('''
                    CREATE TABLE invoice_counter (
                        fy_code TEXT PRIMARY KEY,
                        counter INTEGER NOT NULL DEFAULT 1
                    )
                ''')
                # Preserve the old counter under the current financial year
                from datetime import datetime
                fy_code = get_financial_year_code()
                cur.execute(
                    "INSERT INTO invoice_counter (fy_code, counter) VALUES (%s, %s)",
                    (fy_code, old_counter)
                )
            else:
                # New schema — create if not exists
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS invoice_counter (
                        fy_code TEXT PRIMARY KEY,
                        counter INTEGER NOT NULL DEFAULT 1
                    )
                ''')

            # --- students ---
            cur.execute('''
                CREATE TABLE IF NOT EXISTS students (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE,
                    phone TEXT UNIQUE,
                    name TEXT,
                    address TEXT,
                    alt_phone TEXT,
                    course TEXT,
                    duration TEXT,
                    joining_date TEXT,
                    fee INTEGER,
                    discount INTEGER,
                    approved_text TEXT,
                    total_installments INTEGER,
                    salutation TEXT,
                    validity TEXT
                )
            ''')

            # --- payments ---
            cur.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    student_id INTEGER,
                    invoice_no TEXT,
                    amount INTEGER,
                    payment_date TEXT,
                    installment_text TEXT,
                    FOREIGN KEY (student_id) REFERENCES students (id)
                )
            ''')

            # Safely add new columns to existing databases (idempotent)
            migrations = {
                'students': [
                    ('total_installments', 'INTEGER'),
                    ('approved_text', 'TEXT'),
                    ('salutation', 'TEXT'),
                    ('validity', 'TEXT'),
                ],
                'payments': [
                    ('installment_text', 'TEXT'),
                ],
            }
            for table, cols in migrations.items():
                for col_name, col_type in cols:
                    cur.execute(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                    )

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Student helpers
# ---------------------------------------------------------------------------
def save_student_db(data):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM students WHERE email = %s OR phone = %s",
                (data.get("email"), data.get("phone"))
            )
            student = cur.fetchone()

            if student:
                cur.execute('''
                    UPDATE students SET
                        name=%s, address=%s, alt_phone=%s, course=%s, duration=%s,
                        joining_date=%s, validity=%s, total_installments=%s, salutation=%s
                    WHERE id=%s
                ''', (
                    data.get("name"), data.get("address"), data.get("alt_phone"),
                    data.get("course"), data.get("duration"), data.get("joining_date"),
                    data.get("validity"), data.get("total_installments"), data.get("salutation"),
                    student['id']
                ))
                s_id = student['id']
            else:
                cur.execute('''
                    INSERT INTO students
                        (name, email, phone, address, alt_phone, course, duration,
                         joining_date, validity, total_installments, salutation)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                ''', (
                    data.get("name"), data.get("email"), data.get("phone"),
                    data.get("address"), data.get("alt_phone"), data.get("course"),
                    data.get("duration"), data.get("joining_date"), data.get("validity"),
                    data.get("total_installments"), data.get("salutation")
                ))
                s_id = cur.fetchone()['id']

            # Used only during data migrations / imports
            if "payments" in data:
                for p in data["payments"]:
                    cur.execute('''
                        INSERT INTO payments (student_id, invoice_no, amount, payment_date, installment_text)
                        VALUES (%s, %s, %s, %s, %s)
                    ''', (s_id, p["invoice"], p["amount"], p["date"], p.get("installment", "")))

        conn.commit()
        return s_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Run DB init on startup
# ---------------------------------------------------------------------------
init_db()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def home():
    return render_template("registration.html")


@app.route('/registration', methods=['GET', 'POST'])
def registration():
    if request.method == 'POST':
        data = {
            "name": request.form.get("name", "").strip(),
            "address": request.form.get("address", "").strip(),
            "email": request.form.get("email", "").strip(),
            "phone": request.form.get("phone", "").strip(),
            "alt_phone": request.form.get("alt_phone", "").strip(),
            "course": request.form.get("course", "").strip(),
            "duration": request.form.get("duration", "").strip(),
            "joining_date": request.form.get("joining_date", "").strip(),
            "validity": request.form.get("validity", "").strip(),
            "salutation": request.form.get("salutation", "Mr.").strip(),
            "previous_total_paid": int(request.form.get("previous_total_paid", 0)),
            "total_installments": int(request.form.get("total_installments", 1)),
            "next_installment": request.form.get("next_installment", "1"),
            "fee_preset": int(request.form.get("fee_preset", 0)),
            "discount_preset": int(request.form.get("discount_preset", 0))
        }

        if not data["name"]:
            return "Name cannot be blank", 400
        if not data["email"] or "@" not in data["email"]:
            return "Valid email is required", 400
        if not data["phone"] or len(data["phone"]) < 10:
            return "Valid 10-digit phone number is required", 400

        s_id = save_student_db(data)

        # Fetch ACTUAL history from DB (prevents state mismatch if search was skipped)
        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM students WHERE id = %s", (s_id,))
                student = cur.fetchone()
                cur.execute("SELECT amount FROM payments WHERE student_id = %s", (s_id,))
                payments = cur.fetchall()

                total_paid = sum(p['amount'] for p in payments)
                data["previous_total_paid"] = total_paid
                data["fee_preset"] = student['fee'] if student['fee'] is not None else 0
                data["discount_preset"] = student['discount'] if student['discount'] is not None else 0
                data["total_installments"] = student['total_installments'] or data.get("total_installments", 1)

                num_paid = len(payments)
                total_allowed = data["total_installments"]
                if num_paid >= total_allowed:
                    data["next_installment"] = "Payment Completed"
                else:
                    data["next_installment"] = str(num_paid + 1)

                from datetime import datetime
                if not data.get("joining_date"):
                    data["joining_date"] = datetime.now().strftime("%Y-%m-%d")
        finally:
            conn.close()

        return render_template("form.html", prefill=data)
    return render_template("registration.html")


@app.route('/search_student')
def search_student():
    query = request.args.get('query', '').strip().lower()
    if not query:
        return {"success": False, "message": "Query required"}, 400

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. Try exact match first
            cur.execute('''
                SELECT * FROM students
                WHERE lower(name) = %s OR lower(email) = %s OR phone = %s OR alt_phone = %s
            ''', (query, query, query, query))
            student = cur.fetchone()

            # 2. Fuzzy fallback
            if not student:
                search_query = f"%{query}%"
                cur.execute('''
                    SELECT * FROM students
                    WHERE lower(name) LIKE %s OR lower(email) LIKE %s
                       OR phone LIKE %s OR alt_phone LIKE %s
                ''', (search_query, search_query, search_query, search_query))
                student = cur.fetchone()

            if student:
                student_data = dict(student)
                cur.execute(
                    "SELECT amount FROM payments WHERE student_id = %s",
                    (student['id'],)
                )
                payments = cur.fetchall()

                total_paid = sum(p['amount'] for p in payments)
                student_data["previous_total_paid"] = total_paid

                total_allowed = student_data.get("total_installments") or 1
                student_data["total_installments"] = total_allowed

                num_paid = len(payments)
                if num_paid >= total_allowed:
                    student_data["next_installment"] = "Payment Completed"
                else:
                    student_data["next_installment"] = str(num_paid + 1)

                student_data["fee_preset"] = student['fee'] if student['fee'] is not None else 0
                student_data["discount_preset"] = student['discount'] if student['discount'] is not None else 0
                student_data["validity"] = student['validity'] or ""

                return {"success": True, "data": student_data}
    finally:
        conn.close()

    return {"success": False, "message": "Student not found"}, 404


@app.route('/proceed_to_billing', methods=['POST'])
def proceed_to_billing():
    data = request.json
    return render_template("form.html", prefill=data)


@app.route('/delete_student', methods=['POST'])
def delete_student():
    data = request.json
    query = data.get('query', '').strip().lower()
    if not query:
        return {"success": False, "message": "Query required"}, 400

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''
                SELECT id, name FROM students
                WHERE lower(email) = %s OR phone = %s OR alt_phone = %s
            ''', (query, query, query))
            student = cur.fetchone()

            if not student:
                return {
                    "success": False,
                    "message": "No exact match found. Please provide an exact email or phone number to delete."
                }, 404

            s_id = student['id']
            s_name = student['name']

            cur.execute("DELETE FROM payments WHERE student_id = %s", (s_id,))
            cur.execute("DELETE FROM students WHERE id = %s", (s_id,))

        conn.commit()
        return {
            "success": True,
            "message": f"Student '{s_name}' and all associated records have been successfully deleted."
        }
    finally:
        conn.close()


@app.route('/receipt', methods=["POST"])
def receipt():

    def safe_int(val):
        try:
            return int(float(val)) if val else 0
        except (ValueError, TypeError):
            return 0

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()

    paid_amount = safe_int(request.form.get("paid_amount"))
    already_paid = safe_int(request.form.get("already_paid"))
    total_fee = safe_int(request.form.get("fee"))
    discount = safe_int(request.form.get("discount"))

    # --- Duplicate-payment guard (safe on page refresh) ---
    existing_invoice = None
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM students WHERE email = %s OR phone = %s",
                (email, phone)
            )
            student = cur.fetchone()
            if student:
                s_id = student['id']
                from datetime import datetime
                raw_date = request.form.get("invoice_date")
                try:
                    search_date = datetime.strptime(raw_date, "%Y-%m-%d").strftime("%d-%m-%Y")
                except Exception:
                    search_date = raw_date

                cur.execute('''
                    SELECT invoice_no FROM payments
                    WHERE student_id = %s AND amount = %s
                      AND payment_date = %s AND installment_text = %s
                    ORDER BY id DESC LIMIT 1
                ''', (s_id, paid_amount, search_date, request.form.get("approved")))
                payment = cur.fetchone()
                if payment:
                    existing_invoice = payment['invoice_no']
    finally:
        conn.close()

    invoice_no = existing_invoice if existing_invoice else get_next_invoice_number(invoice_date=request.form.get("joining_date"), increment=True)

    # --- Calculate balance ---
    balance = (total_fee - discount) - (already_paid + paid_amount)

    from num2words import num2words
    try:
        amount_in_words = num2words(paid_amount, lang='en_IN').title() + " Only"
    except Exception:
        amount_in_words = ""

    from datetime import datetime

    def format_date(date_str):
        if not date_str:
            return ""
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y")
        except Exception:
            return date_str

    data = {
        "invoice": invoice_no,
        "invoice_date": format_date(request.form.get("invoice_date")),
        "joining_date": format_date(request.form.get("joining_date")),
        "validity": format_date(request.form.get("validity")),
        "approved": request.form.get("approved"),
        "salutation": request.form.get("salutation", ""),

        "payment_method": request.form.get("payment_method"),
        "reference": request.form.get("reference", "NA"),

        "name": request.form.get("name"),
        "address": request.form.get("address"),
        "email": request.form.get("email"),
        "phone": request.form.get("phone"),
        "alt_phone": request.form.get("alt_phone"),

        "course": request.form.get("course"),
        "duration": request.form.get("duration"),
        "installment": request.form.get("installment"),

        "fee": total_fee,
        "discount": discount,
        "paid_amount": paid_amount,
        "already_paid": already_paid,
        "balance": balance,
        "amount_in_words": f"Rupees {amount_in_words}"
    }

    # --- Persist payment to DB ---
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM students WHERE email = %s OR phone = %s",
                (request.form.get("email"), request.form.get("phone"))
            )
            student = cur.fetchone()

            if student and not existing_invoice:
                s_id = student['id']
                cur.execute(
                    "UPDATE students SET fee=%s, discount=%s, approved_text=%s, total_installments=%s WHERE id=%s",
                    (total_fee, discount, request.form.get("approved"), request.form.get("installment"), s_id)
                )
                cur.execute('''
                    INSERT INTO payments (student_id, invoice_no, amount, payment_date, installment_text)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (s_id, invoice_no, paid_amount, data["invoice_date"], request.form.get("approved")))

        conn.commit()
    finally:
        conn.close()

    # Load signature as base64 for reliable PDF rendering
    sig_b64 = ""
    sig_path = os.path.join(app.static_folder, "Ashna_sign.png")
    if os.path.exists(sig_path):
        with open(sig_path, "rb") as f:
            sig_b64 = "data:image/png;base64," + base64.b64encode(f.read()).decode()

    return render_template("receipt.html", data=data, signature_b64=sig_b64)


@app.route('/students')
def student_list():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM students ORDER BY id DESC")
            students_raw = cur.fetchall()
            result = []
            for s in students_raw:
                cur.execute(
                    "SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE student_id = %s",
                    (s['id'],)
                )
                total_paid = cur.fetchone()['total'] or 0
                fee = s['fee'] or 0
                discount = s['discount'] or 0
                net_fee = fee - discount
                balance = max(net_fee - total_paid, 0)

                if net_fee == 0:
                    status = 'Pending'
                elif balance == 0:
                    status = 'Completed'
                else:
                    status = 'Partial'

                result.append({
                    'id': s['id'],
                    'salutation': s['salutation'] or '',
                    'name': s['name'],
                    'email': s['email'],
                    'phone': s['phone'],
                    'course': s['course'],
                    'duration': s['duration'],
                    'joining_date': s['joining_date'],
                    'fee': fee,
                    'discount': discount,
                    'total_paid': total_paid,
                    'balance': balance,
                    'status': status,
                })
    finally:
        conn.close()
    return render_template("students.html", students=result)


if __name__ == '__main__':
    app.run(debug=True)
