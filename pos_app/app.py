from flask import Flask, render_template, request, redirect, session, jsonify
import pymysql
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)
app.secret_key = 'gP#9xK@mZ2!qLv8nRw4$TjYe6&uBcDf'


def get_db():
    return pymysql.connect(
        host="localhost",
        user="root",
        password="",
        database="pos_system",
        cursorclass=pymysql.cursors.DictCursor
    )


# ✅ ADDED — Activity Log Helper
def log_activity(cashier_id, shop_id, action, description=""):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO activity_logs (cashier_id, shop_id, action, description)
            VALUES (%s, %s, %s, %s)
        """, (cashier_id, shop_id, action, description))
        conn.commit()
        cursor.close()
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
                       WHERE u.username=%s""", (username,))
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


# ---------- CASHIER POS (UC-001) ----------
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
                       WHERE si.shop_id = %s AND si.stock > 0""", (shop_id,))
    else:
        cur.execute("SELECT id, name, price, stock FROM items WHERE stock > 0")
    items = cur.fetchall()
    db.close()
    return jsonify(items)


@app.route('/api/create_order', methods=['POST'])
def create_order():
    data = request.json
    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO transactions (cashier_id, shop_id, total, payment_method) VALUES (%s, %s, %s, 'pending')",
                (session['user_id'], session.get('shop_id'), data['total']))
    db.commit()
    txn_id = cur.lastrowid
    for item in data['items']:
        cur.execute("INSERT INTO transaction_items (transaction_id, item_id, quantity, subtotal) VALUES (%s,%s,%s,%s)",
                    (txn_id, item['id'], item['qty'], item['subtotal']))
    db.commit()
    db.close()
    return jsonify({"success": True, "transaction_id": txn_id})


# ---------- PAYMENT (UC-002) ----------
@app.route('/payment/<int:txn_id>', methods=['GET', 'POST'])
def payment(txn_id):
    db = get_db()
    cur = db.cursor()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'cancel':
            cur.execute("DELETE FROM transaction_items WHERE transaction_id=%s", (txn_id,))
            cur.execute("DELETE FROM transactions WHERE id=%s AND payment_method='pending'", (txn_id,))
            db.commit()
            db.close()
            # ✅ ADDED — Log void/cancel
            log_activity(session['user_id'], session.get('shop_id'), 'VOID',
                         f"Order #{txn_id} cancelled")
            return redirect('/cashier/pos')

        if action == 'confirm':
            method = request.form['method']
            cur.execute("UPDATE transactions SET payment_method=%s WHERE id=%s", (method, txn_id))

            cur.execute("SELECT shop_id, total FROM transactions WHERE id=%s", (txn_id,))
            txn = cur.fetchone()
            shop_id = txn['shop_id']
            total = txn['total']

            cur.execute("SELECT item_id, quantity FROM transaction_items WHERE transaction_id=%s", (txn_id,))
            for row in cur.fetchall():
                if shop_id:
                    cur.execute(
                        "UPDATE shop_inventory SET stock = stock - %s WHERE shop_id=%s AND item_id=%s",
                        (row['quantity'], shop_id, row['item_id'])
                    )
                else:
                    cur.execute(
                        "UPDATE items SET stock = stock - %s WHERE id=%s",
                        (row['quantity'], row['item_id'])
                    )

            db.commit()
            db.close()
            # ✅ ADDED — Log sale
            log_activity(session['user_id'], session.get('shop_id'), 'SALE',
                         f"Order #{txn_id} completed via {method} — ₱{float(total):.2f}")
            return redirect('/receipt/' + str(txn_id))

    cur.execute("SELECT total FROM transactions WHERE id=%s", (txn_id,))
    txn = cur.fetchone()
    db.close()
    return render_template('payment.html', txn_id=txn_id, total=txn['total'])


# ---------- RECEIPT ----------
@app.route('/receipt/<int:txn_id>')
def receipt(txn_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("""SELECT t.*, 
                          COALESCE(u.full_name, u.username) as cashier_name,
                          s.name as shop_name
                   FROM transactions t
                   JOIN users u ON t.cashier_id = u.id
                   LEFT JOIN shops s ON t.shop_id = s.id
                   WHERE t.id=%s""", (txn_id,))
    txn = cur.fetchone()
    cur.execute("""SELECT i.name, ti.quantity, ti.subtotal 
                   FROM transaction_items ti 
                   JOIN items i ON ti.item_id = i.id 
                   WHERE ti.transaction_id=%s""", (txn_id,))
    items = cur.fetchall()
    db.close()
    return render_template('receipt.html', txn=txn, items=items)


# ---------- ADMIN DASHBOARD (UC-003) ----------
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

    query = """SELECT t.*, 
                      COALESCE(u.full_name, u.username) as cashier_name,
                      s.name as shop_name
               FROM transactions t
               JOIN users u ON t.cashier_id = u.id
               LEFT JOIN shops s ON t.shop_id = s.id
               WHERE 1=1"""
    params = []

    if date_from:
        query += " AND DATE(t.created_at) >= %s"
        params.append(date_from)
    if date_to:
        query += " AND DATE(t.created_at) <= %s"
        params.append(date_to)
    if cashier_filter:
        query += " AND t.cashier_id=%s"
        params.append(cashier_filter)
    if shop_filter:
        query += " AND t.shop_id=%s"
        params.append(shop_filter)

    query += " ORDER BY t.created_at DESC"
    cur.execute(query, params)
    sales = cur.fetchall()

    rev_query = "SELECT SUM(total) as filtered_total FROM transactions WHERE 1=1"
    rev_params = []
    if date_from:
        rev_query += " AND DATE(created_at) >= %s"
        rev_params.append(date_from)
    if date_to:
        rev_query += " AND DATE(created_at) <= %s"
        rev_params.append(date_to)
    if cashier_filter:
        rev_query += " AND cashier_id=%s"
        rev_params.append(cashier_filter)
    if shop_filter:
        rev_query += " AND shop_id=%s"
        rev_params.append(shop_filter)
    cur.execute(rev_query, rev_params)
    filtered_total = cur.fetchone()['filtered_total'] or 0

    today_query = "SELECT SUM(total) as day_total FROM transactions WHERE DATE(created_at)=CURDATE()"
    today_params = []
    if shop_filter:
        today_query += " AND shop_id=%s"
        today_params.append(shop_filter)
    cur.execute(today_query, today_params)
    day_total = cur.fetchone()['day_total'] or 0

    month_query = """SELECT SUM(total) as month_total FROM transactions 
                     WHERE MONTH(created_at)=MONTH(CURDATE()) 
                     AND YEAR(created_at)=YEAR(CURDATE())"""
    month_params = []
    if shop_filter:
        month_query += " AND shop_id=%s"
        month_params.append(shop_filter)
    cur.execute(month_query, month_params)
    month_total = cur.fetchone()['month_total'] or 0

    cur.execute("SELECT id, COALESCE(full_name, username) as name FROM users WHERE role='cashier'")
    cashier_list = cur.fetchall()
    cur.execute("SELECT * FROM shops ORDER BY name")
    shop_list = cur.fetchall()
    db.close()

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

    try:
        db2 = get_db()
        cur2 = db2.cursor()
        cur2.execute("""
            SELECT DATE(created_at) as sale_date, SUM(total) as daily_total
            FROM transactions
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            GROUP BY DATE(created_at)
            ORDER BY sale_date ASC
        """)
        daily_sales = cur2.fetchall()
        db2.close()
    except Exception:
        daily_sales = []

    chart_labels = [str(row['sale_date']) for row in daily_sales]
    chart_data = [float(row['daily_total']) for row in daily_sales]

    return render_template('admin_dashboard.html',
                           sales=sales,
                           chart_labels=chart_labels,
                           chart_data=chart_data,
                           username=session['username'],
                           cashier_list=cashier_list,
                           shop_list=shop_list,
                           day_total=day_total,
                           month_total=month_total,
                           filtered_total=filtered_total,
                           filter_label=filter_label,
                           selected_cashier=cashier_filter,
                           selected_date_from=date_from,
                           selected_date_to=date_to,
                           selected_shop=shop_filter)


# ---------- ADD ITEM (multi-shop) ----------
@app.route('/admin/add_item', methods=['GET', 'POST'])
def add_item():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM shops ORDER BY name")
    shops = cur.fetchall()

    success = None
    error = None

    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        price    = request.form.get('price', '0')
        shop_ids = request.form.getlist('shop_ids')

        if not name or not price:
            error = "Item name and price are required."
        elif not shop_ids:
            error = "Please select at least one shop."
        else:
            cur.execute("INSERT INTO items (name, price) VALUES (%s, %s)", (name, price))
            db.commit()
            item_id = cur.lastrowid

            for shop_id in shop_ids:
                stock     = request.form.get(f'stock_{shop_id}', 0)
                min_stock = request.form.get(f'min_stock_{shop_id}', 5)
                cur.execute("""
                    INSERT INTO shop_inventory (shop_id, item_id, stock, min_stock, is_active)
                    VALUES (%s, %s, %s, %s, 1)
                """, (shop_id, item_id, stock, min_stock))

            db.commit()
            db.close()
            success = name

    return render_template('add_item.html',
                           shops=shops,
                           success=success,
                           error=error)


# ---------- INVENTORY (UC-004) ----------
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

        if action == 'add_item':
            cur.execute("INSERT INTO items (name, price, stock, min_stock) VALUES (%s,%s,%s,%s)",
                        (request.form['name'], request.form['price'],
                         request.form['stock'], request.form['min_stock']))
            db.commit()
            new_item_id = cur.lastrowid
            cur.execute("""INSERT INTO shop_inventory (shop_id, item_id, stock, min_stock, is_active)
                           VALUES (%s,%s,%s,%s,1)""",
                        (shop_id, new_item_id,
                         request.form['stock'], request.form['min_stock']))
            db.commit()

        elif action == 'update_stock':
            cur.execute("UPDATE shop_inventory SET stock=%s WHERE shop_id=%s AND item_id=%s",
                        (request.form['stock'], request.form['shop_id'], request.form['item_id']))
            db.commit()

        elif action == 'delete_item':
            cur.execute("DELETE FROM items WHERE id=%s", (request.form['item_id'],))
            db.commit()

        return redirect('/admin/inventory' + (f'?shop_id={shop_id}' if shop_id else ''))

    cur.execute("""SELECT si.shop_id, i.id, i.name, i.price,
                          si.stock, si.min_stock
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
        inventory_data[sid].append({
            'id': row['id'],
            'name': row['name'],
            'price': float(row['price']),
            'stock': row['stock'],
            'min_stock': row['min_stock']
        })

    cur.execute("""SELECT i.name as item_name, s.name as shop_name,
                          si.stock, si.min_stock
                   FROM shop_inventory si
                   JOIN items i ON si.item_id = i.id
                   JOIN shops s ON si.shop_id = s.id
                   WHERE si.stock <= si.min_stock
                   AND si.is_active = 1
                   ORDER BY s.name""")
    low_stock = cur.fetchall()

    cur.execute("SELECT * FROM shops ORDER BY name")
    shop_list = cur.fetchall()

    db.close()
    return render_template('inventory.html',
                           inventory_data=inventory_data,
                           low_stock=low_stock,
                           shop_list=shop_list,
                           selected_shop=selected_shop)


# ---------- SHOP MANAGEMENT ----------
@app.route('/admin/shops', methods=['GET', 'POST'])
def shops():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            cur.execute("INSERT INTO shops (name, address) VALUES (%s,%s)",
                        (request.form['name'], request.form['address']))
            db.commit()
        elif action == 'delete':
            cur.execute("DELETE FROM shops WHERE id=%s", (request.form['shop_id'],))
            db.commit()
    cur.execute("""SELECT s.*, COUNT(u.id) as cashier_count 
                   FROM shops s 
                   LEFT JOIN users u ON s.id = u.shop_id AND u.role='cashier'
                   GROUP BY s.id
                   ORDER BY s.created_at DESC""")
    shops = cur.fetchall()
    db.close()
    return render_template('shops.html', shops=shops, username=session['username'])


# ---------- CASHIER MANAGEMENT ----------
@app.route('/admin/cashiers')
def cashiers():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    cur.execute("""SELECT u.*, s.name as shop_name 
                   FROM users u
                   LEFT JOIN shops s ON u.shop_id = s.id
                   WHERE u.role='cashier' 
                   ORDER BY u.created_at DESC""")
    cashiers = cur.fetchall()
    cur.execute("SELECT * FROM shops ORDER BY name")
    shops = cur.fetchall()
    db.close()
    return render_template('cashiers.html', cashiers=cashiers, shops=shops, username=session['username'])


@app.route('/admin/cashiers/add', methods=['POST'])
def add_cashier():
    if session.get('role') != 'admin':
        return redirect('/login')
    full_name = request.form['full_name']
    username = request.form['username']
    password = request.form['password']
    shop_id = request.form['shop_id'] or None
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id FROM users WHERE username=%s", (username,))
    if cur.fetchone():
        db.close()
        return redirect('/admin/cashiers?error=Username already exists')
    cur.execute("INSERT INTO users (full_name, username, password, role, shop_id) VALUES (%s,%s,%s,'cashier',%s)",
            (full_name, username, generate_password_hash(password), shop_id))
    db.commit()
    db.close()
    return redirect('/admin/cashiers?success=Cashier added successfully')


@app.route('/admin/cashiers/delete', methods=['POST'])
def delete_cashier():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM users WHERE id=%s AND role='cashier'", (request.form['cashier_id'],))
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
        cur.execute("UPDATE items SET name=%s, price=%s WHERE id=%s",
                    (request.form['name'], request.form['price'], item_id))

        cur.execute("SELECT id FROM shops")
        shops = cur.fetchall()
        for shop in shops:
            sid = shop['id']
            stock = request.form.get(f'stock_{sid}', 0)
            min_stock = request.form.get(f'min_stock_{sid}', 5)

            cur.execute("SELECT id FROM shop_inventory WHERE shop_id=%s AND item_id=%s", (sid, item_id))
            exists = cur.fetchone()

            if exists:
                cur.execute("""UPDATE shop_inventory SET stock=%s, min_stock=%s 
                               WHERE shop_id=%s AND item_id=%s""",
                            (stock, min_stock, sid, item_id))
            else:
                cur.execute("""INSERT INTO shop_inventory (shop_id, item_id, stock, min_stock) 
                               VALUES (%s, %s, %s, %s)""",
                            (sid, item_id, stock, min_stock))

        db.commit()
        db.close()
        return redirect('/admin/inventory')

    cur.execute("SELECT * FROM items WHERE id=%s", (item_id,))
    item = cur.fetchone()

    cur.execute("""SELECT s.id, s.name,
                          COALESCE(si.stock, 0) as stock,
                          COALESCE(si.min_stock, 5) as min_stock
                   FROM shops s
                   LEFT JOIN shop_inventory si ON s.id = si.shop_id AND si.item_id=%s
                   ORDER BY s.name""", (item_id,))
    shop_stocks = cur.fetchall()

    db.close()
    return render_template('edit_item.html', item=item, shop_stocks=shop_stocks)


# ---------- TRANSACTION DETAIL ----------
@app.route('/admin/transaction/<int:txn_id>')
def transaction_detail(txn_id):
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()
    cur.execute("""SELECT t.*,
                          COALESCE(u.full_name, u.username) as cashier_name,
                          s.name as shop_name
                   FROM transactions t
                   JOIN users u ON t.cashier_id = u.id
                   LEFT JOIN shops s ON t.shop_id = s.id
                   WHERE t.id=%s""", (txn_id,))
    txn = cur.fetchone()
    cur.execute("""SELECT i.name, ti.quantity, ti.subtotal
                   FROM transaction_items ti
                   JOIN items i ON ti.item_id = i.id
                   WHERE ti.transaction_id=%s""", (txn_id,))
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

        cur.execute("SELECT * FROM users WHERE id=%s", (session['user_id'],))
        user = cur.fetchone()

        if not check_password_hash(user['password'], current):
            error = "Current password is incorrect."
        elif new_pass != confirm:
            error = "New passwords do not match."
        elif len(new_pass) < 4:
            error = "Password must be at least 4 characters."
        else:
            cur.execute("UPDATE users SET password=%s WHERE id=%s",
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

        cur.execute("SELECT id FROM users WHERE username=%s AND id != %s",
                    (username, user_id))
        if cur.fetchone():
            error = "Username already taken by another user."
        else:
            if new_pass:
                    cur.execute("""UPDATE users SET full_name=%s, username=%s,
                    password=%s, shop_id=%s WHERE id=%s""",
                    (full_name, username, generate_password_hash(new_pass), shop_id, user_id))
            else:
                cur.execute("""UPDATE users SET full_name=%s, username=%s,
                               shop_id=%s WHERE id=%s""",
                            (full_name, username, shop_id, user_id))
            db.commit()
            db.close()
            return redirect('/admin/cashiers')

    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    cashier = cur.fetchone()
    cur.execute("SELECT * FROM shops ORDER BY name")
    shops = cur.fetchall()
    db.close()
    return render_template('edit_cashier.html',
                           cashier=cashier, shops=shops, error=error)


# ---------- SALES PER ITEM REPORT ----------
@app.route('/admin/reports/items')
def sales_per_item():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()

    shop_filter = request.args.get('shop_id', '')

    if shop_filter:
        cur.execute("""SELECT i.name as item_name,
                              s.name as shop_name,
                              SUM(ti.quantity) as total_qty,
                              SUM(ti.subtotal) as total_sales
                       FROM transaction_items ti
                       JOIN items i ON ti.item_id = i.id
                       JOIN transactions t ON ti.transaction_id = t.id
                       JOIN shops s ON t.shop_id = s.id
                       WHERE t.shop_id = %s
                       GROUP BY i.id, s.id
                       ORDER BY total_sales DESC""", (shop_filter,))
    else:
        cur.execute("""SELECT i.name as item_name,
                              s.name as shop_name,
                              SUM(ti.quantity) as total_qty,
                              SUM(ti.subtotal) as total_sales
                       FROM transaction_items ti
                       JOIN items i ON ti.item_id = i.id
                       JOIN transactions t ON ti.transaction_id = t.id
                       JOIN shops s ON t.shop_id = s.id
                       GROUP BY i.id, s.id
                       ORDER BY total_sales DESC""")

    report = cur.fetchall()
    cur.execute("SELECT * FROM shops ORDER BY name")
    shops = cur.fetchall()
    db.close()
    return render_template('sales_report.html',
                           report=report, shops=shops,
                           selected_shop=shop_filter)


# ---------- ACTIVITY LOGS (UC-NEW) ----------
@app.route('/admin/activity-logs')
def admin_activity_logs():
    if session.get('role') != 'admin':
        return redirect('/login')
    db = get_db()
    cur = db.cursor()

    shop_id      = request.args.get('shop_id', '')
    action       = request.args.get('action', '')
    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')

    query = """
        SELECT al.*, u.full_name AS cashier_name, s.name AS shop_name
        FROM activity_logs al
        JOIN users u  ON al.cashier_id = u.id
        JOIN shops s  ON al.shop_id    = s.id
        WHERE 1=1
    """
    params = []

    if shop_id:
        query += " AND al.shop_id = %s"
        params.append(shop_id)
    if action:
        query += " AND al.action = %s"
        params.append(action)
    if date_from:
        query += " AND DATE(al.created_at) >= %s"
        params.append(date_from)
    if date_to:
        query += " AND DATE(al.created_at) <= %s"
        params.append(date_to)

    query += " ORDER BY al.created_at DESC LIMIT 500"
    cur.execute(query, params)
    logs = cur.fetchall()

    cur.execute("SELECT id, name FROM shops ORDER BY name")
    shops = cur.fetchall()

    db.close()
    return render_template('activity_logs.html',
                           logs=logs, shops=shops,
                           selected_shop=shop_id,
                           selected_action=action,
                           date_from=date_from,
                           date_to=date_to)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
