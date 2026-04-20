from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'gP#9xK@mZ2!qLv8nRw4$TjYe6&uBcDf'

DB_PATH = os.path.join(os.path.dirname(__file__), 'pos_system.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS shops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'cashier',
        shop_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (shop_id) REFERENCES shops(id)
    );

    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER DEFAULT 0,
        min_stock INTEGER DEFAULT 5,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS shop_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        stock INTEGER DEFAULT 0,
        min_stock INTEGER DEFAULT 5,
        is_active INTEGER DEFAULT 1,
        FOREIGN KEY (shop_id) REFERENCES shops(id),
        FOREIGN KEY (item_id) REFERENCES items(id)
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cashier_id INTEGER NOT NULL,
        shop_id INTEGER,
        total REAL NOT NULL,
        payment_method TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (cashier_id) REFERENCES users(id),
        FOREIGN KEY (shop_id) REFERENCES shops(id)
    );

    CREATE TABLE IF NOT EXISTS transaction_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        subtotal REAL NOT NULL,
        FOREIGN KEY (transaction_id) REFERENCES transactions(id),
        FOREIGN KEY (item_id) REFERENCES items(id)
    );

    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cashier_id INTEGER NOT NULL,
        shop_id INTEGER,
        action TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (cashier_id) REFERENCES users(id),
        FOREIGN KEY (shop_id) REFERENCES shops(id)
    );
    """)

    # Seed default admin if not exists
    cur.execute("SELECT id FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute("INSERT INTO shops (name, address) VALUES ('Main Shop', 'Main Branch')")
        shop_id = cur.lastrowid
        cur.execute("INSERT INTO users (full_name, username, password, role, shop_id) VALUES (?, ?, ?, 'admin', ?)",
                    ('Administrator', 'admin', generate_password_hash('admin123'), shop_id))
    conn.commit()
    conn.close()

def log_activity(cashier_id, shop_id, action, description=""):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO activity_logs (cashier_id, shop_id, action, description) VALUES (?,?,?,?)",
                    (cashier_id, shop_id, action, description))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[log_activity error] {e}")

# ---------- LOGIN ----------
@app.route('/')
def index():
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        cur = db.cursor()
        cur.execute("""SELECT u.*, s.name as shop_name
            FROM users u
            LEFT JOIN shops s ON u.shop_id = s.id
            WHERE u.username=?""", (username,))
        user = cur.fetchone()
        if user and not check_password_hash(user['password'], password):
            user = None
        db.close()
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['full_name'] = user['full_name'] or user['username']
            session['shop_id'] = user['shop_id']
            session['shop_name'] = user['shop_name'] or 'Unassigned'
            if user['role'] == 'admin':
                return redirect('/admin/dashboard')
            else:
                return redirect('/cashier/pos')
        else:
            error = "Invalid username or password."
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ---------- CASHIER POS ----------
@app.route('/cashier/pos')
def cashier_pos():
    if session.get('role') != 'cashier':
        return redirect('/login')
    return render_template('cashier_pos.html',
                           username=session['full_name'],
                           shop_name=session.get('shop_name', ''))

@app.route('/api/items')
def get_items():
    db = get_db()
    cur = db.cursor()
    shop_id = session.get('shop_id')
    if shop_id:
        cur.execute("""SELECT i.id, i.name, i.price, si.stock
            FROM shop_inventory si
            JOIN items i ON si.item_id = i.id
            WHERE si.shop_id = ? AND si.stock > 0""", (shop_id,))
    else:
        cur.execute("SELECT id, name, price, stock FROM items WHERE stock > 0")
    items = [dict(row) for row in cur.fetchall()]
    db.close()
    return jsonify(items)

@app.route('/api/create_order', methods=['POST'])
def create_order():
    data = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO transactions (cashier_id, shop_id, total, payment_method) VALUES (?,?,?,'pending')",
                (session['user_id'], session.get('shop_id'), data['total']))
    db.commit()
    txn_id = cur.lastrowid
    for item in data['items']:
        cur.execute("INSERT INTO transaction_items (transaction_id, item_id, quantity, subtotal) VALUES (?,?,?,?)",
                    (txn_id, item['id'], item['qty'], item['subtotal']))
    db.commit()
    db.close()
    return jsonify({"success": True, "transaction_id": txn_id})

# ---------- PAYMENT ----------
@app.route('/payment/<int:txn_id>', methods=['GET', 'POST'])
def payment(txn_id):
    db = get_db()
    cur = db.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'cancel':
            cur.execute("DELETE FROM transaction_items WHERE transaction_id=?", (txn_id,))
            cur.execute("DELETE FROM transactions WHERE id=? AND payment_method='pending'", (txn_id,))
            db.commit()
            db.close()
            log_activity(session['user_id'], session.get('shop_id'), 'VOID', f"Order #{txn_id} cancelled")
            return redirect('/cashier/pos')
        if action == 'confirm':
            method = request.form['method']
            cur.execute("UPDATE transactions SET payment_method=? WHERE id=?", (method, txn_id))
            cur.execute("SELECT shop_id, total FROM transactions WHERE id=?", (txn_id,))
            txn = cur.fetchone()
            shop_id = txn['shop_id']
            total = txn['total']
            cur.execute("SELECT item_id, quantity FROM transaction_items WHERE transaction_id=?", (txn_id,))
            for row in cur.fetchall():
                if shop_id:
                    cur.execute("UPDATE shop_inventory SET stock = stock - ? WHERE shop_id=? AND item_id=?",
                                (row['quantity'], shop_id, row['item_id']))
                else:
                    cur.execute("UPDATE items SET stock = stock - ? WHERE id=?",
                                (row['quantity'], row['item_id']))
            db.commit()
            db.close()
            log_activity(session['user_id'], session.get('shop_id'), 'SALE',
                         f"Order #{txn_id} completed via {method} — ₱{float(total):.2f}")
            return redirect('/receipt/' + str(txn_id))
    cur.execute("SELECT total FROM transactions WHERE id=?", (txn_id,))
    txn = cur.fetchone()
    db.close()
    return render_template('payment.html', txn_id=txn_id, total=txn['total'])

# ---------- RECEIPT ----------
@app.route('/receipt/<int:txn_id>')
def receipt(txn_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("""SELECT t.*, COALESCE(u.full_name, u.username) as cashier_name, s.name as shop_name
        FROM transactions t
        JOIN users u ON t.cashier_id = u.id
        LEFT JOIN shops s ON t.shop_id = s.id
        WHERE t.id=?""", (txn_id,))
    txn = cur.fetchone()
    cur.execute("""SELECT i.name, ti.quantity, ti.subtotal
        FROM transaction_items ti
        JOIN items i ON ti.item_id = i.id
        WHERE ti.transaction_id=?""", (txn_id,))
    items = cur.fetchall()
    db.close()
    return render_template('receipt.html', txn=txn, items=items)

# ---------- ADMIN DASHBOARD ----------
@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    cashier_filter = request.args.get('cashier_id', '')
    shop_filter = request.args.get('shop_id', '')

    query = """SELECT t.*, COALESCE(u.full_name, u.username) as cashier_name, s.name as shop_name
        FROM transactions t
        JOIN users u ON t.cashier_id = u.id
        LEFT JOIN shops s ON t.shop_id = s.id
        WHERE 1=1"""
    params = []
    if date_from:
        query += " AND DATE(t.created_at) >= ?"
        params.append(date_from)
    if date_to:
        query += " AND DATE(t.created_at) <= ?"
        params.append(date_to)
    if cashier_filter:
        query += " AND t.cashier_id=?"
        params.append(cashier_filter)
    if shop_filter:
        query += " AND t.shop_id=?"
        params.append(shop_filter)
    query += " ORDER BY t.created_at DESC"
    cur.execute(query, params)
    sales = [dict(r) for r in cur.fetchall()]

    rev_query = "SELECT SUM(total) as filtered_total FROM transactions WHERE 1=1"
    rev_params = []
    if date_from:
        rev_query += " AND DATE(created_at) >= ?"
        rev_params.append(date_from)
    if date_to:
        rev_query += " AND DATE(created_at) <= ?"
        rev_params.append(date_to)
    if cashier_filter:
        rev_query += " AND cashier_id=?"
        rev_params.append(cashier_filter)
    if shop_filter:
        rev_query += " AND shop_id=?"
        rev_params.append(shop_filter)
    cur.execute(rev_query, rev_params)
    filtered_total = cur.fetchone()['filtered_total'] or 0

    cur.execute("SELECT SUM(total) as day_total FROM transactions WHERE DATE(created_at)=DATE('now')" +
                (" AND shop_id=?" if shop_filter else ""), ([shop_filter] if shop_filter else []))
    day_total = cur.fetchone()['day_total'] or 0

    cur.execute("SELECT SUM(total) as month_total FROM transactions WHERE strftime('%Y-%m', created_at)=strftime('%Y-%m','now')" +
                (" AND shop_id=?" if shop_filter else ""), ([shop_filter] if shop_filter else []))
    month_total = cur.fetchone()['month_total'] or 0

    cur.execute("SELECT id, COALESCE(full_name, username) as name FROM users WHERE role='cashier'")
    cashier_list = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM shops ORDER BY name")
    shop_list = [dict(r) for r in cur.fetchall()]

    cur.execute("""SELECT DATE(created_at) as sale_date, SUM(total) as daily_total
        FROM transactions
        WHERE created_at >= DATE('now', '-7 days')
        GROUP BY DATE(created_at)
        ORDER BY sale_date ASC""")
    daily_sales = cur.fetchall()
    db.close()

    chart_labels = [str(row['sale_date']) for row in daily_sales]
    chart_data = [float(row['daily_total']) for row in daily_sales]

    active_filters = []
    if date_from or date_to:
        if date_from and date_to:
            active_filters.append(f"{date_from} to {date_to}")
        elif date_from:
            active_filters.append(f"From {date_from}")
        elif date_to:
            active_filters.append(f"Until {date_to}")
    if shop_filter:
        active_filters.append("Shop")
    if cashier_filter:
        active_filters.append("Cashier")
    filter_label = "Filtered: " + ", ".join(active_filters) if active_filters else "All Time Total"

    return render_template('admin_dashboard.html',
                           sales=sales, chart_labels=chart_labels, chart_data=chart_data,
                           username=session['username'], cashier_list=cashier_list,
                           shop_list=shop_list, day_total=day_total, month_total=month_total,
                           filtered_total=filtered_total, filter_label=filter_label,
                           selected_cashier=cashier_filter, selected_date_from=date_from,
                           selected_date_to=date_to, selected_shop=shop_filter)

# ---------- ADD ITEM ----------
@app.route('/admin/add_item', methods=['GET', 'POST'])
def add_item():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM shops ORDER BY name")
    shops = [dict(r) for r in cur.fetchall()]
    success = None
    error = None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        price = request.form.get('price', '0')
        shop_ids = request.form.getlist('shop_ids')
        if not name or not price:
            error = "Item name and price are required."
        elif not shop_ids:
            error = "Please select at least one shop."
        else:
            cur.execute("INSERT INTO items (name, price) VALUES (?,?)", (name, price))
            db.commit()
            item_id = cur.lastrowid
            for sid in shop_ids:
                stock = request.form.get(f'stock_{sid}', 0)
                min_stock = request.form.get(f'min_stock_{sid}', 5)
                cur.execute("INSERT INTO shop_inventory (shop_id, item_id, stock, min_stock, is_active) VALUES (?,?,?,?,1)",
                            (sid, item_id, stock, min_stock))
            db.commit()
            db.close()
            success = name
    return render_template('add_item.html', shops=shops, success=success, error=error)

# ---------- INVENTORY ----------
@app.route('/admin/inventory', methods=['GET', 'POST'])
def inventory():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    selected_shop = request.args.get('shop_id', '')
    if request.method == 'POST':
        action = request.form.get('action')
        shop_id = request.form.get('shop_id')
        if action == 'update_stock':
            cur.execute("UPDATE shop_inventory SET stock=? WHERE shop_id=? AND item_id=?",
                        (request.form['stock'], request.form['shop_id'], request.form['item_id']))
            db.commit()
        elif action == 'delete_item':
            cur.execute("DELETE FROM items WHERE id=?", (request.form['item_id'],))
            db.commit()
        db.close()
        return redirect('/admin/inventory' + (f'?shop_id={shop_id}' if shop_id else ''))

    cur.execute("""SELECT si.shop_id, i.id, i.name, i.price, si.stock, si.min_stock
        FROM shop_inventory si
        JOIN items i ON si.item_id = i.id
        WHERE si.is_active = 1
        ORDER BY si.shop_id, i.name""")
    rows = cur.fetchall()
    inventory_data = {}
    for row in rows:
        sid = row['shop_id']
        if sid not in inventory_data:
            inventory_data[sid] = []
        inventory_data[sid].append({'id': row['id'], 'name': row['name'],
                                    'price': float(row['price']), 'stock': row['stock'],
                                    'min_stock': row['min_stock']})

    cur.execute("""SELECT i.name as item_name, s.name as shop_name, si.stock, si.min_stock
        FROM shop_inventory si
        JOIN items i ON si.item_id = i.id
        JOIN shops s ON si.shop_id = s.id
        WHERE si.stock <= si.min_stock AND si.is_active = 1
        ORDER BY s.name""")
    low_stock = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM shops ORDER BY name")
    shop_list = [dict(r) for r in cur.fetchall()]
    db.close()
    return render_template('inventory.html', inventory_data=inventory_data,
                           low_stock=low_stock, shop_list=shop_list, selected_shop=selected_shop)

# ---------- SHOPS ----------
@app.route('/admin/shops', methods=['GET', 'POST'])
def shops():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            cur.execute("INSERT INTO shops (name, address) VALUES (?,?)",
                        (request.form['name'], request.form['address']))
            db.commit()
        elif action == 'delete':
            cur.execute("DELETE FROM shops WHERE id=?", (request.form['shop_id'],))
            db.commit()
    cur.execute("""SELECT s.*, COUNT(u.id) as cashier_count
        FROM shops s
        LEFT JOIN users u ON s.id = u.shop_id AND u.role='cashier'
        GROUP BY s.id ORDER BY s.created_at DESC""")
    shops_list = [dict(r) for r in cur.fetchall()]
    db.close()
    return render_template('shops.html', shops=shops_list, username=session['username'])

# ---------- CASHIERS ----------
@app.route('/admin/cashiers')
def cashiers():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    cur.execute("""SELECT u.*, s.name as shop_name
        FROM users u LEFT JOIN shops s ON u.shop_id = s.id
        WHERE u.role='cashier' ORDER BY u.created_at DESC""")
    cashiers_list = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM shops ORDER BY name")
    shops_list = [dict(r) for r in cur.fetchall()]
    db.close()
    return render_template('cashiers.html', cashiers=cashiers_list, shops=shops_list, username=session['username'])

@app.route('/admin/cashiers/add', methods=['POST'])
def add_cashier():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id FROM users WHERE username=?", (request.form['username'],))
    if cur.fetchone():
        db.close()
        return redirect('/admin/cashiers?error=Username already exists')
    shop_id = request.form['shop_id'] or None
    cur.execute("INSERT INTO users (full_name, username, password, role, shop_id) VALUES (?,?,?,'cashier',?)",
                (request.form['full_name'], request.form['username'],
                 generate_password_hash(request.form['password']), shop_id))
    db.commit()
    db.close()
    return redirect('/admin/cashiers?success=Cashier added successfully')

@app.route('/admin/cashiers/delete', methods=['POST'])
def delete_cashier():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM users WHERE id=? AND role='cashier'", (request.form['cashier_id'],))
    db.commit()
    db.close()
    return redirect('/admin/cashiers')

# ---------- EDIT ITEM ----------
@app.route('/admin/inventory/edit/<int:item_id>', methods=['GET', 'POST'])
def edit_item(item_id):
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    if request.method == 'POST':
        cur.execute("UPDATE items SET name=?, price=? WHERE id=?",
                    (request.form['name'], request.form['price'], item_id))
        cur.execute("SELECT id FROM shops")
        shops_for_edit = [row['id'] for row in cur.fetchall()]
        for sid in shops_for_edit:
            stock = request.form.get(f'stock_{sid}', 0)
            min_stock = request.form.get(f'min_stock_{sid}', 5)
            cur2 = db.cursor()
            cur2.execute("SELECT id FROM shop_inventory WHERE shop_id=? AND item_id=?", (sid, item_id))
            if cur2.fetchone():
                cur.execute("UPDATE shop_inventory SET stock=?, min_stock=? WHERE shop_id=? AND item_id=?",
                            (stock, min_stock, sid, item_id))
            else:
                cur.execute("INSERT INTO shop_inventory (shop_id, item_id, stock, min_stock) VALUES (?,?,?,?)",
                            (sid, item_id, stock, min_stock))
        db.commit()
        db.close()
        return redirect('/admin/inventory')
    cur.execute("SELECT * FROM items WHERE id=?", (item_id,))
    item = cur.fetchone()
    cur.execute("""SELECT s.id, s.name, COALESCE(si.stock,0) as stock, COALESCE(si.min_stock,5) as min_stock
        FROM shops s
        LEFT JOIN shop_inventory si ON s.id=si.shop_id AND si.item_id=?
        ORDER BY s.name""", (item_id,))
    shop_stocks = [dict(r) for r in cur.fetchall()]
    db.close()
    return render_template('edit_item.html', item=dict(item), shop_stocks=shop_stocks)

# ---------- TRANSACTION DETAIL ----------
@app.route('/admin/transaction/<int:txn_id>')
def transaction_detail(txn_id):
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    cur.execute("""SELECT t.*, COALESCE(u.full_name, u.username) as cashier_name, s.name as shop_name
        FROM transactions t
        JOIN users u ON t.cashier_id = u.id
        LEFT JOIN shops s ON t.shop_id = s.id
        WHERE t.id=?""", (txn_id,))
    txn = cur.fetchone()
    cur.execute("""SELECT i.name, ti.quantity, ti.subtotal
        FROM transaction_items ti JOIN items i ON ti.item_id=i.id
        WHERE ti.transaction_id=?""", (txn_id,))
    items = cur.fetchall()
    db.close()
    return render_template('transaction_detail.html', txn=txn, items=items)

# ---------- CHANGE PASSWORD ----------
@app.route('/cashier/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    error = None
    success = None
    if request.method == 'POST':
        current = request.form['current_password']
        new_pass = request.form['new_password']
        confirm = request.form['confirm_password']
        cur.execute("SELECT * FROM users WHERE id=?", (session['user_id'],))
        user = cur.fetchone()
        if not check_password_hash(user['password'], current):
            error = "Current password is incorrect."
        elif new_pass != confirm:
            error = "New passwords do not match."
        elif len(new_pass) < 4:
            error = "Password must be at least 4 characters."
        else:
            cur.execute("UPDATE users SET password=? WHERE id=?",
                        (generate_password_hash(new_pass), session['user_id']))
            db.commit()
            success = "Password changed successfully!"
    db.close()
    return render_template('change_password.html', error=error, success=success)

# ---------- EDIT CASHIER ----------
@app.route('/admin/cashiers/edit/<int:user_id>', methods=['GET', 'POST'])
def edit_cashier(user_id):
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    error = None
    if request.method == 'POST':
        full_name = request.form['full_name']
        username = request.form['username']
        shop_id = request.form['shop_id']
        new_pass = request.form.get('new_password', '').strip()
        cur.execute("SELECT id FROM users WHERE username=? AND id != ?", (username, user_id))
        if cur.fetchone():
            error = "Username already taken by another user."
        else:
            if new_pass:
                cur.execute("UPDATE users SET full_name=?, username=?, password=?, shop_id=? WHERE id=?",
                            (full_name, username, generate_password_hash(new_pass), shop_id, user_id))
            else:
                cur.execute("UPDATE users SET full_name=?, username=?, shop_id=? WHERE id=?",
                            (full_name, username, shop_id, user_id))
            db.commit()
            db.close()
            return redirect('/admin/cashiers')
    cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
    cashier = cur.fetchone()
    cur.execute("SELECT * FROM shops ORDER BY name")
    shops_list = [dict(r) for r in cur.fetchall()]
    db.close()
    return render_template('edit_cashier.html', cashier=dict(cashier) if cashier else None, shops=shops_list, error=error)

# ---------- SALES REPORT ----------
@app.route('/admin/reports/items')
def sales_per_item():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    shop_filter = request.args.get('shop_id', '')
    if shop_filter:
        cur.execute("""SELECT i.name as item_name, s.name as shop_name,
            SUM(ti.quantity) as total_qty, SUM(ti.subtotal) as total_sales
            FROM transaction_items ti
            JOIN items i ON ti.item_id=i.id
            JOIN transactions t ON ti.transaction_id=t.id
            JOIN shops s ON t.shop_id=s.id
            WHERE t.shop_id=?
            GROUP BY i.id, s.id ORDER BY total_sales DESC""", (shop_filter,))
    else:
        cur.execute("""SELECT i.name as item_name, s.name as shop_name,
            SUM(ti.quantity) as total_qty, SUM(ti.subtotal) as total_sales
            FROM transaction_items ti
            JOIN items i ON ti.item_id=i.id
            JOIN transactions t ON ti.transaction_id=t.id
            JOIN shops s ON t.shop_id=s.id
            GROUP BY i.id, s.id ORDER BY total_sales DESC""")
    report = cur.fetchall()
    cur.execute("SELECT * FROM shops ORDER BY name")
    shops_list = [dict(r) for r in cur.fetchall()]
    db.close()
    return render_template('sales_report.html', report=[dict(r) for r in report], shops=shops_list, selected_shop=shop_filter)

# ---------- ACTIVITY LOGS ----------
@app.route('/admin/activity-logs')
def admin_activity_logs():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    shop_id = request.args.get('shop_id', '')
    action = request.args.get('action', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    query = """SELECT al.*, u.full_name AS cashier_name, s.name AS shop_name
        FROM activity_logs al
        JOIN users u ON al.cashier_id=u.id
        JOIN shops s ON al.shop_id=s.id
        WHERE 1=1"""
    params = []
    if shop_id:
        query += " AND al.shop_id=?"
        params.append(shop_id)
    if action:
        query += " AND al.action=?"
        params.append(action)
    if date_from:
        query += " AND DATE(al.created_at) >= ?"
        params.append(date_from)
    if date_to:
        query += " AND DATE(al.created_at) <= ?"
        params.append(date_to)
    query += " ORDER BY al.created_at DESC LIMIT 500"
    cur.execute(query, params)
    logs = cur.fetchall()
    cur.execute("SELECT id, name FROM shops ORDER BY name")
    shops_list = [dict(r) for r in cur.fetchall()]
    db.close()
    return render_template('activity_logs.html', logs=[dict(r) for r in logs], shops=shops_list,
                           selected_shop=shop_id, selected_action=action,
                           date_from=date_from, date_to=date_to)

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)

# Trigger DB init on import (for gunicorn)
init_db()
