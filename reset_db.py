import os
import sqlite3

DATABASE = "billing.db"

def reset_database():
    print("Initializing database reset...")
    
    # 1. Clear SQLite Database
    if os.path.exists(DATABASE):
        try:
            conn = sqlite3.connect(DATABASE)
            cursor = conn.cursor()
            
            # Drop tables
            cursor.execute("DROP TABLE IF EXISTS payments")
            cursor.execute("DROP TABLE IF EXISTS students")
            
            conn.commit()
            conn.close()
            print(f"✔ Tables dropped successfully in {DATABASE}")
        except Exception as e:
            print(f"✘ Error clearing database: {e}")
    else:
        print(f"ℹ No database file found at {DATABASE}")

    # 2. Reset Invoice Counter
    if os.path.exists("invoice.txt"):
        try:
            with open("invoice.txt", "w") as f:
                f.write("1")
            print("✔ Invoice counter reset to 1 in invoice.txt")
        except Exception as e:
            print(f"✘ Error resetting invoice: {e}")
    else:
        print("ℹ No invoice.txt file found to reset.")

    print("\nReset Complete! You can now start 'app.py' for a fresh session.")

if __name__ == "__main__":
    confirm = input("Are you sure you want to CLEAR the entire database? (yes/no): ")
    if confirm.lower() == 'yes':
        reset_database()
    else:
        print("Reset cancelled.")
