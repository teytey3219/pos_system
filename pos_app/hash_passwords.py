import pymysql
from werkzeug.security import generate_password_hash

db = pymysql.connect(host="localhost", user="root", password="", database="pos_system", cursorclass=pymysql.cursors.DictCursor)
cur = db.cursor()
cur.execute("SELECT id, password FROM users")
users = cur.fetchall()
for u in users:
    hashed = generate_password_hash(u['password'])
    cur.execute("UPDATE users SET password=%s WHERE id=%s", (hashed, u['id']))
db.commit()
db.close()
print("Done! All passwords hashed.")
