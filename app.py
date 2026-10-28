import os
import io
import csv
import json
import secrets
import sqlite3
import time
import re
import threading
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from contextlib import contextmanager
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    import qrcode
except ImportError:
    qrcode = None

APP_DIR = os.path.abspath(os.path.dirname(__file__))
# Tiny .env loader: put WHATSAPP_TOKEN=..., GOOGLE_CLIENT_ID=... etc. in a file called .env next to app.py.
try:
    with open(os.path.join(APP_DIR, '.env'), encoding='utf-8') as _envf:
        for _line in _envf:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
except OSError:
    pass
DB_PATH = os.path.join(APP_DIR, 'store.db')
UPLOAD_DIR = os.path.join(APP_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
CATEGORIES = ['Boys Wear', 'Girls Wear', 'Kids Wear', 'Chaniya Choli', 'Lehenga Choli', 'Anarkali Kurtas', 'Jewelry / Accessories', 'Lehenga', 'Blouse']

app = Flask(__name__)
def load_secret_key():
    # Environment secret is preferred. Otherwise, create a private persistent key so
    # cookie sessions survive restarts without shipping a predictable hard-coded key.
    configured = os.environ.get('SHREEJEE_SECRET_KEY', '').strip()
    if configured:
        return configured
    secret_path = os.path.join(APP_DIR, '.secret_key')
    try:
        with open(secret_path, 'r', encoding='utf-8') as secret_file:
            existing = secret_file.read().strip()
            if len(existing) >= 32:
                return existing
    except OSError:
        pass
    generated = secrets.token_urlsafe(48)
    try:
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as secret_file:
            secret_file.write(generated)
        return generated
    except FileExistsError:
        with open(secret_path, 'r', encoding='utf-8') as secret_file:
            return secret_file.read().strip()
    except OSError:
        # Deployment environments should set SHREEJEE_SECRET_KEY if the folder is read-only.
        return generated

app.config['SECRET_KEY'] = load_secret_key()
app.config['MAX_CONTENT_LENGTH'] = 72 * 1024 * 1024  # room for multipart overhead around up to eight product photos
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE', '0') == '1'
# Speed: static files are versioned with ?v=..., so browsers/the Android app may cache them for a week.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(days=7)


@app.after_request
def gzip_text_responses(response):
    """Compress HTML/CSS/JS/JSON so pages load much faster through the Cloudflare tunnel on phones."""
    try:
        if response.status_code != 200 or 'Content-Encoding' in response.headers:
            return response
        if 'gzip' not in request.headers.get('Accept-Encoding', '').lower():
            return response
        mimetype = response.mimetype or ''
        if not (mimetype.startswith('text/') or mimetype in ('application/json', 'application/javascript', 'image/svg+xml')):
            return response
        response.direct_passthrough = False
        data = response.get_data()
        if len(data) < 600:
            return response
        import gzip as _gzip
        packed = _gzip.compress(data, compresslevel=6)
        response.set_data(packed)
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Content-Length'] = str(len(packed))
        response.headers.add('Vary', 'Accept-Encoding')
    except Exception:
        pass
    return response


def external_store_url(path):
    """Build a public URL for QR payloads, honoring PUBLIC_BASE_URL and Cloudflare HTTPS."""
    configured = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/')
    if configured:
        return configured + (path if path.startswith('/') else '/' + path)
    host = (request.headers.get('X-Forwarded-Host') or request.host or '').split(',')[0].strip()
    proto = (request.headers.get('X-Forwarded-Proto') or request.scheme or 'http').split(',')[0].strip().lower()
    if proto not in ('http', 'https'):
        proto = 'https' if host.lower().endswith('.trycloudflare.com') else 'http'
    if host.lower().endswith('.trycloudflare.com'):
        proto = 'https'
    return f'{proto}://{host}{path}'


def create_qr_data_uri(payload):
    """Return a PNG data URI; failures are logged and surfaced by the order page."""
    if not payload or qrcode is None:
        return None
    try:
        code = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=9,
            border=4,
        )
        code.add_data(payload)
        code.make(fit=True)
        image = code.make_image(fill_color='#111827', back_color='white')
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        import base64
        return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
    except Exception:
        app.logger.exception('QR image generation failed')
        return None

DEMO_CUSTOMER_EMAIL = 'default.customer@shreejee.local'
OWNER_PHONE = '8340453008'  # Rupali Acharya - shown on the site and used for WhatsApp
RETURN_WINDOW_DAYS = 7
RETURN_REASONS = ['Size or fit problem', 'Damaged or defective item', 'Wrong item received',
                  'Colour or design different from photos', 'Quality not as expected', 'Changed my mind', 'Other']

STORE = {
    'name': 'Shreejee Fashion',
    'owner': 'Rupali Acharya',
    'address': 'Near Jora Pathak Road, Shakti-Mandir, opposite Chhota Gurdwara, beside Ashirwad Tower, Sanghvi Colony, House No. 04.',
    'since': '2021',
}

SITE_EDITOR_DEFAULTS = {
    'site_name': 'Shreejee Fashion', 'logo_text': 'SF', 'logo_filename': '', 'hero_image': '',
    'owner': 'Rupali Acharya',
    'address': STORE['address'], 'phone': '', 'hours': 'Contact the store for current hours',
    'since': '2021', 'announcement': 'Welcome to Shreejee Fashion — styles for the whole family.',
    'hero_enabled': True, 'hero_eyebrow': 'YOUR EVERYDAY STYLE DESTINATION',
    'hero_heading': 'Wear your', 'hero_emphasis': 'confidence.',
    'hero_description': 'Fresh essentials, everyday favourites and family styles — all in one place. Proudly serving you since 2021.',
    'hero_primary_text': 'Explore collection', 'hero_secondary_text': 'Meet the store',
    'shop_heading': 'The latest essentials.', 'buy_intro': 'Styles available to buy.',
    'rent_intro': 'Styles available to rent.', 'footer_text': 'Style that feels like you. Serving the community since 2021.',
    'nav_shop': 'Home', 'nav_buy': 'Buy', 'nav_rent': 'Rent',
    'nav_boys': 'Boys Wear', 'nav_girls': 'Girls Wear', 'nav_kids': 'Kids Wear',
    'nav_shop_url': '/', 'nav_buy_url': '/?view=buy', 'nav_rent_url': '/?view=rent',
    'nav_boys_url': '/?category=Boys+Wear', 'nav_girls_url': '/?category=Girls+Wear', 'nav_kids_url': '/?category=Kids+Wear',
    'menu_categories': [
        {'label': 'Boys / Men', 'type': 'coming_soon', 'category': 'Boys Wear', 'children': []},
        {'label': 'Women / Girl', 'type': 'group', 'category': '', 'children': [
            {'label': 'Chania-choli', 'category': 'Chaniya Choli'},
            {'label': 'Lehenga Choli', 'category': 'Lehenga Choli'},
            {'label': 'Jewelry / Accessories', 'category': 'Jewelry / Accessories'},
            {'label': 'Lehenga', 'category': 'Lehenga'},
            {'label': 'Blouse', 'category': 'Blouse'},
        ]},
        {'label': 'Kids Wear', 'type': 'coming_soon', 'category': 'Kids Wear', 'children': []},
    ],
    'story_eyebrow': 'OUR STORY', 'story_heading': 'A little style.', 'story_emphasis': 'A lot of heart.',
    'story_description': 'Family fashion, selected with care and made for everyday life.',
    'category_order': 'Boys Wear, Girls Wear, Kids Wear, Chaniya Choli, Lehenga Choli, Anarkali Kurtas, Jewelry / Accessories, Lehenga, Blouse',
    'font_family': 'DM Sans', 'base_font_size': 14, 'heading_scale': 1.0, 'button_style': 'solid', 'background_mode': 'solid', 'gradient_end': '#ebe7dd', 'default_ui': 'classic',
    'primary_color': '#1c3d31', 'accent_color': '#c7854a', 'text_color': '#202923',
    'page_background': '#f8f6f0', 'card_background': '#fffdf8', 'button_radius': 3, 'card_radius': 0, 'card_gap': 22,
    'product_button_order': 'buy,rent,cart,save,whatsapp,review', 'product_button_hidden': '',
    'buy_page_text': 'Find everyday favourites to buy and keep.',
    'rent_page_text': 'Discover occasionwear and rental styles. Availability is confirmed by the store.',
}


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


DELIVERED_STATES = ('Delivered', 'Completed')
DEMO_CUSTOMER_ADDRESS = 'Shreejee Fashion Store, Jora Pathak Road, Shakti Mandir, Dhanbad, Jharkhand 826001'


class StockError(Exception):
    pass


def restore_stock(con, order):
    """Give units back to stock when an order is cancelled."""
    try:
        items = json.loads(order['items_json'])
    except (TypeError, json.JSONDecodeError):
        return
    for item in items:
        try:
            con.execute('UPDATE products SET stock=stock+?, updated_at=? WHERE id=? AND stock IS NOT NULL', (max(0, int(item.get('qty', 0))), now(), int(item.get('id'))))
        except (TypeError, ValueError):
            continue
ETA_INPUT_FORMAT = '%Y-%m-%dT%H:%M'


def format_eta(order):
    """Human friendly estimated arrival text set by the admin (empty when not set)."""
    try:
        eta_at = (order['eta_at'] or '').strip()
        note = (order['eta_note'] or '').strip()
    except (KeyError, IndexError, TypeError):
        return ''
    parts = []
    if eta_at:
        try:
            parts.append(datetime.strptime(eta_at, ETA_INPUT_FORMAT).strftime('%a, %d %b %Y, %I:%M %p').replace(' 0', ' '))
        except ValueError:
            pass
    if note:
        parts.append(note)
    return ' · '.join(parts)


app.jinja_env.filters['eta_text'] = format_eta


def get_site_config(draft=False):
    key = 'site_editor_draft' if draft else 'site_editor_published'
    try:
        with db() as con:
            row = con.execute('SELECT value FROM site_settings WHERE key=?', (key,)).fetchone()
        saved = json.loads(row['value']) if row else {}
        result = dict(SITE_EDITOR_DEFAULTS)
        if isinstance(saved, dict):
            result.update(saved)
        return result
    except (sqlite3.Error, json.JSONDecodeError, TypeError):
        return dict(SITE_EDITOR_DEFAULTS)


def save_site_config(config, draft=False):
    key = 'site_editor_draft' if draft else 'site_editor_published'
    value = json.dumps(config, ensure_ascii=False)
    with db() as con:
        con.execute('INSERT INTO site_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


def safe_local_path(value, fallback='/'):
    value = (value or '').strip()
    if value.startswith('/') and not value.startswith('//') and '\\' not in value and not value.startswith('/\\'):
        return value
    return fallback


def add_status_history(con, order_id, old_status, new_status, old_payment, new_payment, actor_id, note=''):
    if old_status == new_status and old_payment == new_payment:
        return
    con.execute('''INSERT INTO order_status_history(order_id,old_status,new_status,old_payment_status,new_payment_status,actor_user_id,note,created_at)
                   VALUES(?,?,?,?,?,?,?,?)''', (order_id, old_status, new_status, old_payment, new_payment, actor_id, note[:500], now()))


def set_order_state(con, order, status=None, payment_status=None, actor_id=None, note=''):
    new_status = status if status is not None else order['status']
    new_payment = payment_status if payment_status is not None else order['payment_status']
    if order['status'] in ('Delivered', 'Completed') and (new_status != order['status'] or new_payment != order['payment_status']):
        raise ValueError('Delivered/completed orders are already complete and cannot be changed.')
    con.execute('UPDATE orders SET status=?, payment_status=?, updated_at=? WHERE id=?', (new_status, new_payment, now(), order['id']))
    add_status_history(con, order['id'], order['status'], new_status, order['payment_status'], new_payment, actor_id, note)


def sanitize_menu_categories(value):
    """Return a small, safe, editable menu tree used by the public navigation."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            value = []
    if not isinstance(value, list):
        return []
    result = []
    for raw in value[:24]:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get('label', '')).strip()[:60]
        kind = str(raw.get('type', 'category')).strip().lower()
        if not label or kind not in ('category', 'group', 'coming_soon'):
            continue
        category = str(raw.get('category', '')).strip()[:90]
        children = []
        if kind == 'group':
            for child in (raw.get('children') if isinstance(raw.get('children'), list) else [])[:30]:
                if not isinstance(child, dict):
                    continue
                child_label = str(child.get('label', '')).strip()[:60]
                child_category = str(child.get('category', '')).strip()[:90]
                if child_label:
                    children.append({'label': child_label, 'category': child_category or child_label})
        else:
            children = []
            category = category or label
        result.append({'label': label, 'type': kind, 'category': category, 'children': children})
    return result


def locked_menu_category_names(config=None):
    """Return product-category names that are marked Coming Soon in the customer menu."""
    config = config if isinstance(config, dict) else get_site_config()
    return {
        item['category'] for item in sanitize_menu_categories(config.get('menu_categories', []))
        if item.get('type') == 'coming_soon' and item.get('category')
    }


def customer_category_locked(category, config=None):
    """True when a public-facing category is explicitly marked Coming Soon."""
    return bool(category and category in locked_menu_category_names(config))


def clean_settings(form, base):
    result = dict(base)
    lengths = {
        'site_name': 100, 'logo_text': 8, 'owner': 100, 'address': 300, 'phone': 40, 'hours': 120,
        'since': 12, 'announcement': 400, 'hero_eyebrow': 100, 'hero_heading': 100,
        'hero_emphasis': 100, 'hero_description': 600, 'hero_primary_text': 50, 'hero_secondary_text': 50,
        'shop_heading': 120, 'buy_intro': 250, 'rent_intro': 250, 'footer_text': 300,
        'nav_shop': 40, 'nav_buy': 40, 'nav_rent': 40, 'nav_boys': 40, 'nav_girls': 40, 'nav_kids': 40,
        'story_eyebrow': 100, 'story_heading': 100, 'story_emphasis': 100, 'story_description': 500,
        'category_order': 500, 'buy_page_text': 300, 'rent_page_text': 300,
    }
    for key, maxlen in lengths.items():
        if key in form:
            result[key] = str(form.get(key, '')).strip()[:maxlen]
    if 'menu_categories_json' in form:
        result['menu_categories'] = sanitize_menu_categories(form.get('menu_categories_json', '[]'))
    else:
        result['menu_categories'] = sanitize_menu_categories(result.get('menu_categories', []))
    safe_nav_defaults = {'nav_shop_url':'/','nav_buy_url':'/?view=buy','nav_rent_url':'/?view=rent','nav_boys_url':'/?category=Boys+Wear','nav_girls_url':'/?category=Girls+Wear','nav_kids_url':'/?category=Kids+Wear'}
    for key, fallback in safe_nav_defaults.items():
        result[key] = safe_local_path(str(form.get(key, result.get(key, fallback)) or '')[:180], fallback)
    allowed_fonts = {'DM Sans', 'Arial', 'Georgia', 'Trebuchet MS', 'Verdana', 'Tahoma', 'system-ui'}
    font = str(form.get('font_family', result.get('font_family', 'DM Sans')))
    result['font_family'] = font if font in allowed_fonts else 'DM Sans'
    for key, fallback in [('primary_color','#1c3d31'),('accent_color','#c7854a'),('text_color','#202923'),('page_background','#f8f6f0'),('card_background','#fffdf8')]:
        value = str(form.get(key, result.get(key, fallback))).strip()
        result[key] = value if re.fullmatch(r'#[0-9a-fA-F]{6}', value) else fallback
    result['button_style'] = form.get('button_style', result.get('button_style', 'solid')) if form.get('button_style', result.get('button_style', 'solid')) in ('solid','outline','soft') else 'solid'
    result['background_mode'] = form.get('background_mode', result.get('background_mode', 'solid')) if form.get('background_mode', result.get('background_mode', 'solid')) in ('solid','soft-gradient') else 'solid'
    requested_default_ui = str(form.get('default_ui', result.get('default_ui', 'classic'))).strip()
    result['default_ui'] = requested_default_ui if requested_default_ui in {'classic', 'modern', 'boutique', 'noir', 'fresh', 'editorial'} else 'classic'
    gradient = str(form.get('gradient_end', result.get('gradient_end', '#ebe7dd'))).strip()
    result['gradient_end'] = gradient if re.fullmatch(r'#[0-9a-fA-F]{6}', gradient) else '#ebe7dd'
    try: result['base_font_size'] = max(12, min(18, int(form.get('base_font_size', result.get('base_font_size', 14)))))
    except (ValueError, TypeError): result['base_font_size'] = 14
    try: result['heading_scale'] = max(0.8, min(1.3, float(form.get('heading_scale', result.get('heading_scale', 1.0)))))
    except (ValueError, TypeError): result['heading_scale'] = 1.0
    for key, low, high, default in [('button_radius',0,30,3),('card_radius',0,36,0),('card_gap',8,40,22)]:
        try: result[key] = max(low, min(high, int(form.get(key, result.get(key, default)))))
        except (ValueError, TypeError): result[key] = default
    result['hero_enabled'] = bool(form.get('hero_enabled'))
    if 'product_button_order' in form:
        known = {k for k, _, _ in PRODUCT_BUTTONS}
        order = []
        for k in str(form.get('product_button_order', '')).split(','):
            k = k.strip()
            if k in known and k not in order:
                order.append(k)
        order += [k for k, _, _ in PRODUCT_BUTTONS if k not in order]
        result['product_button_order'] = ','.join(order)
        can_hide = {k for k, _, h in PRODUCT_BUTTONS if h}
        shown = set(form.getlist('product_button_show'))
        result['product_button_hidden'] = ','.join(k for k in order if k in can_hide and k not in shown)
    return result


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 10000')
    conn.execute('PRAGMA synchronous = NORMAL')
    conn.execute('PRAGMA foreign_keys = ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as con:
        # WAL improves responsiveness when the storefront, admin pages and mobile clients access SQLite together.
        con.execute('PRAGMA journal_mode = WAL')
        con.execute('PRAGMA wal_autocheckpoint = 1000')
        con.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT DEFAULT '',
            address TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'customer',
            verified INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT DEFAULT '',
            price REAL NOT NULL,
            rental_price REAL,
            offer TEXT NOT NULL DEFAULT 'buy',
            image TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL UNIQUE,
            idempotency_key TEXT UNIQUE,
            user_id INTEGER,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            address TEXT NOT NULL,
            items_json TEXT NOT NULL,
            total REAL NOT NULL,
            subtotal REAL NOT NULL DEFAULT 0,
            coupon_code TEXT NOT NULL DEFAULT '',
            discount REAL NOT NULL DEFAULT 0,
            delivery_charge REAL NOT NULL DEFAULT 0,
            delivery_charge_status TEXT NOT NULL DEFAULT '',
            payment_method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Cart QR Ready',
            payment_status TEXT NOT NULL DEFAULT 'Not paid',
            gateway_order_id TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            action_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS site_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_cart_items (
            user_id INTEGER NOT NULL, cart_key TEXT NOT NULL, product_id INTEGER NOT NULL,
            mode TEXT NOT NULL DEFAULT 'buy', size TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL, PRIMARY KEY(user_id, cart_key),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS product_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), review_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(product_id,user_id),
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        ''')
        # Schema upgrades for existing installs.
        user_cols = {row['name'] for row in con.execute('PRAGMA table_info(users)').fetchall()}
        if 'address' not in user_cols:
            con.execute("ALTER TABLE users ADD COLUMN address TEXT NOT NULL DEFAULT ''")
        if 'profile_image' not in user_cols:
            con.execute("ALTER TABLE users ADD COLUMN profile_image TEXT NOT NULL DEFAULT ''")
        notification_cols = {row['name'] for row in con.execute('PRAGMA table_info(notifications)').fetchall()}
        if 'action_url' not in notification_cols:
            con.execute("ALTER TABLE notifications ADD COLUMN action_url TEXT NOT NULL DEFAULT ''")
        con.execute('''CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
            filename TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE)''')
        product_cols = {row['name'] for row in con.execute('PRAGMA table_info(products)').fetchall()}
        if 'rental_price' not in product_cols:
            con.execute('ALTER TABLE products ADD COLUMN rental_price REAL')
        if 'offer' not in product_cols:
            con.execute("ALTER TABLE products ADD COLUMN offer TEXT NOT NULL DEFAULT 'buy'")
        order_cols = {row['name'] for row in con.execute('PRAGMA table_info(orders)').fetchall()}
        for col, ddl in [('subtotal', "ALTER TABLE orders ADD COLUMN subtotal REAL NOT NULL DEFAULT 0"), ('coupon_code', "ALTER TABLE orders ADD COLUMN coupon_code TEXT NOT NULL DEFAULT ''"), ('discount', "ALTER TABLE orders ADD COLUMN discount REAL NOT NULL DEFAULT 0"), ('cod_extra_charge', "ALTER TABLE orders ADD COLUMN cod_extra_charge REAL NOT NULL DEFAULT 0"), ('cod_charge_status', "ALTER TABLE orders ADD COLUMN cod_charge_status TEXT NOT NULL DEFAULT ''"), ('delivery_charge', "ALTER TABLE orders ADD COLUMN delivery_charge REAL NOT NULL DEFAULT 0"), ('delivery_charge_status', "ALTER TABLE orders ADD COLUMN delivery_charge_status TEXT NOT NULL DEFAULT ''")]:
            if col not in order_cols:
                con.execute(ddl)
        # Migrate older COD-only fee fields to the shared delivery-charge fields. Safe to run on every start.
        con.execute("UPDATE orders SET delivery_charge=cod_extra_charge WHERE delivery_charge=0 AND cod_extra_charge>0")
        con.execute("UPDATE orders SET delivery_charge_status=cod_charge_status WHERE delivery_charge_status='' AND cod_charge_status<>''")
        con.execute('''CREATE TABLE IF NOT EXISTS coupons (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, discount_amount REAL NOT NULL DEFAULT 0, min_order REAL NOT NULL DEFAULT 0, max_uses INTEGER NOT NULL DEFAULT 0, used_count INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)''')
        con.execute("INSERT OR IGNORE INTO coupons(code,discount_amount,min_order,max_uses,used_count,active,created_at) VALUES(?,?,?,?,?,?,?)", ('WELCOME2027', 200, 1800, 0, 0, 1, now()))
        cols = {row['name'] for row in con.execute('PRAGMA table_info(orders)').fetchall()}
        if 'gateway_order_id' not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN gateway_order_id TEXT DEFAULT ''")
        if 'confirmation_code' not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN confirmation_code TEXT DEFAULT ''")
        if 'confirmation_code_used' not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN confirmation_code_used INTEGER NOT NULL DEFAULT 0")
        if 'updated_at' not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        if 'confirmation_attempts' not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN confirmation_attempts INTEGER NOT NULL DEFAULT 0")
        if 'upi_expires_at' not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN upi_expires_at TEXT NOT NULL DEFAULT ''")
        if 'eta_at' not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN eta_at TEXT NOT NULL DEFAULT ''")
        if 'eta_note' not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN eta_note TEXT NOT NULL DEFAULT ''")
        # Online-paid orders used to be labelled with the COD status once the store verified payment.
        con.execute("UPDATE orders SET status='Order confirmed' WHERE payment_method='Online' AND status='COD requested'")
        # Legacy COD quotes use the old state names; keep them compatible with the shared fee flow.
        con.execute("UPDATE orders SET updated_at=created_at WHERE updated_at='' OR updated_at IS NULL")
        product_cols = {row['name'] for row in con.execute('PRAGMA table_info(products)').fetchall()}
        if 'updated_at' not in product_cols:
            con.execute("ALTER TABLE products ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        if 'stock' not in product_cols:
            con.execute("ALTER TABLE products ADD COLUMN stock INTEGER")
        if 'max_per_user' not in product_cols:
            con.execute("ALTER TABLE products ADD COLUMN max_per_user INTEGER NOT NULL DEFAULT 0")
        con.execute("UPDATE products SET updated_at=created_at WHERE updated_at='' OR updated_at IS NULL")
        if 'menu_side' not in user_cols:
            con.execute("ALTER TABLE users ADD COLUMN menu_side TEXT NOT NULL DEFAULT 'left'")
        if 'ui_layout' not in user_cols:
            con.execute("ALTER TABLE users ADD COLUMN ui_layout TEXT NOT NULL DEFAULT ''")
        if 'menu_pos' not in user_cols:
            con.execute("ALTER TABLE users ADD COLUMN menu_pos TEXT NOT NULL DEFAULT ''")
            # Customers who had picked the right-hand menu keep it (top-right); everyone else follows their look.
            con.execute("UPDATE users SET menu_pos='tr' WHERE menu_side='right'")
        con.execute('''CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Requested', code_hash TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            issued_at TEXT NOT NULL DEFAULT '', expires_at TEXT NOT NULL DEFAULT '', used_at TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)''')
        if 'sizes' not in product_cols:
            con.execute("ALTER TABLE products ADD COLUMN sizes TEXT NOT NULL DEFAULT ''")
        if 'colours' not in product_cols:
            con.execute("ALTER TABLE products ADD COLUMN colours TEXT NOT NULL DEFAULT ''")
        if 'google_sub' not in user_cols:
            con.execute("ALTER TABLE users ADD COLUMN google_sub TEXT NOT NULL DEFAULT ''")
        con.execute('''CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, ident TEXT NOT NULL, ip TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(kind, ident, created_at)')
        con.execute('''CREATE TABLE IF NOT EXISTS otp_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT NOT NULL, code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL, created_at TEXT NOT NULL)''')
        con.execute('''CREATE TABLE IF NOT EXISTS delivery_areas (
            pin TEXT PRIMARY KEY, area TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)''')
        if not con.execute("SELECT 1 FROM site_settings WHERE key='delivery_seeded'").fetchone():
            con.execute("INSERT OR IGNORE INTO delivery_areas(pin,area,created_at) VALUES('826001','Dhanbad (store area)',?)", (now(),))
            con.execute("INSERT INTO site_settings(key,value) VALUES('delivery_seeded','1')")
        con.execute('''CREATE TABLE IF NOT EXISTS wishlist_items (
            user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(user_id, product_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE)''')
        con.execute('''CREATE TABLE IF NOT EXISTS return_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            product_id INTEGER, product_name TEXT NOT NULL, size TEXT NOT NULL DEFAULT '', line_key TEXT NOT NULL DEFAULT '',
            qty INTEGER NOT NULL DEFAULT 1, kind TEXT NOT NULL DEFAULT 'return', reason TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '', exchange_size TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Requested', refund_status TEXT NOT NULL DEFAULT 'Not applicable',
            refund_amount REAL NOT NULL DEFAULT 0, refund_note TEXT NOT NULL DEFAULT '', admin_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)''')
        con.execute('''CREATE TABLE IF NOT EXISTS order_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
            old_status TEXT NOT NULL DEFAULT '', new_status TEXT NOT NULL DEFAULT '',
            old_payment_status TEXT NOT NULL DEFAULT '', new_payment_status TEXT NOT NULL DEFAULT '',
            actor_user_id INTEGER, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
        )''')
        con.execute("""INSERT INTO order_status_history(order_id,old_status,new_status,old_payment_status,new_payment_status,actor_user_id,note,created_at) SELECT o.id,'',o.status,'',o.payment_status,NULL,'Existing order baseline',COALESCE(o.updated_at,o.created_at) FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_status_history h WHERE h.order_id=o.id)""")
        # Starter admin accounts.
        admins = [
            ('Store Admin', 'admin', 'admin'),
            ('Rajveer Admin', 'rajveer853982@gmail.com', 'rajveer_001'),
        ]
        for name, email, password in admins:
            row = con.execute('SELECT id FROM users WHERE lower(email)=?', (email.lower(),)).fetchone()
            if row:
                # Keep existing password hashes intact. Re-seeding a default password on every
                # server restart would silently undo an administrator's password change.
                con.execute("UPDATE users SET name=?, role='admin', verified=1, blocked=0 WHERE id=?",
                            (name, row['id']))
            else:
                con.execute('INSERT INTO users(name,email,phone,password_hash,role,verified,blocked,created_at) VALUES(?,?,?,?,?,?,?,?)',
                            (name, email.lower(), '', generate_password_hash(password), 'admin', 1, 0, now()))
        # Keep the requested null/null customer identity internal; never advertise it in the public interface.
        demo = con.execute('SELECT id FROM users WHERE lower(email)=?', (DEMO_CUSTOMER_EMAIL,)).fetchone()
        if not demo:
            con.execute('INSERT INTO users(name,email,phone,address,password_hash,role,verified,blocked,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                        ('Customer', DEMO_CUSTOMER_EMAIL, '0000000000', DEMO_CUSTOMER_ADDRESS, generate_password_hash('null'), 'customer', 1, 0, now()))
        else:
            con.execute("UPDATE users SET name='Customer',phone='0000000000',address=?,role='customer',verified=1,blocked=0 WHERE id=?", (DEMO_CUSTOMER_ADDRESS, demo['id']))
        if con.execute('SELECT COUNT(*) FROM products').fetchone()[0] == 0:
            samples = [
                ('Classic Cotton Shirt', 'Boys Wear', 'Everyday cotton shirt. Add sizes and colours when ordering.', 599),
                ('Everyday T-shirt', 'Boys Wear', 'Comfortable casual T-shirt for daily wear.', 299),
                ('Casual Top', 'Girls Wear', 'Versatile top for casual outings.', 449),
                ('Soft Cotton T-shirt', 'Kids Wear', 'Soft, comfortable kids T-shirt.', 249),
            ]
            for name, category, description, price in samples:
                con.execute('INSERT INTO products(name,category,description,price,image,active,created_at) VALUES(?,?,?,?,?,?,?)',
                            (name, category, description, price, '', 1, now()))
        # Carry over Shreejee's original ethnic-wear categories and buy/rent catalogue.
        ethnic_samples = [
            ('Festive Chaniya Choli', 'Chaniya Choli', 'Colourful festive set. Confirm size, colour and rental availability with the store.', 2499, 799, 'both'),
            ('Classic Lehenga Choli', 'Lehenga Choli', 'Elegant occasionwear with a coordinated blouse and dupatta.', 3999, 1299, 'both'),
            ('Anarkali Kurta Set', 'Anarkali Kurtas', 'Graceful Anarkali style for celebrations and family occasions.', 1799, 599, 'both'),
            ('Traditional Jewellery Set', 'Jewelry / Accessories', 'Statement accessories to complete your festive look.', 699, None, 'buy'),
            ('Designer Lehenga', 'Lehenga', 'A special-occasion lehenga. Contact the store for availability.', 4999, 1599, 'both'),
            ('Festive Blouse', 'Blouse', 'Classic blouse styles. Fit, colour and availability confirmed in store.', 899, None, 'buy'),
        ]
        for sample in ethnic_samples:
            if not con.execute('SELECT 1 FROM products WHERE category=? LIMIT 1', (sample[1],)).fetchone():
                con.execute('INSERT INTO products(name,category,description,price,rental_price,offer,image,active,created_at) VALUES(?,?,?,?,?,?,?,1,?)',
                            (*sample[:5], sample[5], '', now()))
        # One-time: give clothing a starter size list (admin can edit or clear it per product afterwards).
        if not con.execute("SELECT 1 FROM site_settings WHERE key='sizes_seeded'").fetchone():
            con.execute("UPDATE products SET sizes='S, M, L, XL' WHERE sizes='' AND lower(category) NOT LIKE '%jewel%' AND lower(category) NOT LIKE '%access%'")
            con.execute("INSERT INTO site_settings(key,value) VALUES('sizes_seeded','1')")
        existing_announcement = con.execute("SELECT value FROM site_settings WHERE key='announcement'").fetchone()
        if not existing_announcement:
            con.execute("INSERT INTO site_settings(key,value) VALUES('announcement',?)", ('Welcome to Shreejee Fashion — styles for the whole family.',))
        elif 'BHAWTARANI' in existing_announcement['value']:
            con.execute("UPDATE site_settings SET value=replace(value,'BHAWTARANI FASHION','Shreejee Fashion') WHERE key='announcement'")
        published_row = con.execute("SELECT value FROM site_settings WHERE key='site_editor_published'").fetchone()
        draft_row = con.execute("SELECT value FROM site_settings WHERE key='site_editor_draft'").fetchone()
        if not published_row:
            config = dict(SITE_EDITOR_DEFAULTS)
            config['announcement'] = existing_announcement['value'] if existing_announcement else config['announcement']
            con.execute('INSERT INTO site_settings(key,value) VALUES(?,?)', ('site_editor_published', json.dumps(config, ensure_ascii=False)))
        if not draft_row:
            con.execute('INSERT INTO site_settings(key,value) VALUES(?,?)', ('site_editor_draft', json.dumps(SITE_EDITOR_DEFAULTS, ensure_ascii=False)))
        # Update only the old default Shop label/URL so existing custom editor settings remain intact.
        for setting_key in ('site_editor_published', 'site_editor_draft'):
            row = con.execute('SELECT value FROM site_settings WHERE key=?', (setting_key,)).fetchone()
            try:
                cfg = json.loads(row['value']) if row else {}
            except (TypeError, json.JSONDecodeError):
                cfg = {}
            changed = False
            if cfg.get('nav_shop') == 'Shop': cfg['nav_shop'] = 'Home'; changed = True
            if cfg.get('nav_shop_url') == '/?browse=1': cfg['nav_shop_url'] = '/'; changed = True
            if changed:
                con.execute('UPDATE site_settings SET value=? WHERE key=?', (json.dumps(cfg, ensure_ascii=False), setting_key))


def parse_sizes(raw):
    """Admin-entered sizes ('S, M, L, XL') -> clean unique list."""
    out, seen = [], set()
    for part in re.split(r'[,;\n]+', str(raw or '')):
        part = part.strip()[:12]
        if part and part.lower() not in seen:
            seen.add(part.lower()); out.append(part)
    return out[:14]


def parse_colours(raw):
    """Admin-entered colours ('Red, Royal Blue') -> clean unique list."""
    out, seen = [], set()
    for part in re.split(r'[,;\n]+', str(raw or '')):
        part = part.strip()[:24]
        if part and part.lower() not in seen:
            seen.add(part.lower()); out.append(part)
    return out[:16]


COLOUR_HEX = {'red': '#c62828', 'maroon': '#7b1e2b', 'pink': '#ec6fa3', 'hot pink': '#e8328c', 'rani pink': '#d81b78', 'magenta': '#b5179e', 'orange': '#ef8a1b', 'mustard': '#d9a520', 'yellow': '#f2cf30', 'gold': '#c9a55c', 'green': '#2e7d32', 'olive': '#6b7a2c', 'mint': '#8fd6b3', 'teal': '#0f8b8d', 'turquoise': '#2bc5c0', 'sky blue': '#6ab7f0', 'blue': '#1e63c9', 'royal blue': '#27479e', 'navy': '#14213d', 'navy blue': '#14213d', 'purple': '#6b3fa0', 'lavender': '#b9a3e3', 'violet': '#7a42c4', 'peach': '#f6b79c', 'cream': '#f3ead3', 'white': '#ffffff', 'off white': '#f4f1e8', 'beige': '#d8c3a0', 'brown': '#7a4a2a', 'grey': '#8a8d91', 'gray': '#8a8d91', 'silver': '#c0c4c8', 'black': '#151515', 'multi': 'conic-gradient(#e53935,#fdd835,#43a047,#1e88e5,#8e24aa,#e53935)', 'multicolour': 'conic-gradient(#e53935,#fdd835,#43a047,#1e88e5,#8e24aa,#e53935)'}


def colour_hex(name):
    return COLOUR_HEX.get(str(name or '').strip().lower(), '')


def resolve_variant(product, size='', colour=''):
    """Validate the customer's size/colour against what the product offers.

    Returns (variant_label, missing) where variant_label is e.g. 'M / Royal Blue' and
    missing is None, 'size' or 'colour'. The label is what is stored in the cart/order 'size' slot."""
    keys = product.keys() if hasattr(product, 'keys') else []
    sizes = parse_sizes(product['sizes'])
    colours = parse_colours(product['colours']) if 'colours' in keys else []
    size = str(size or '').strip(); colour = str(colour or '').strip()
    if ' / ' in size and not colour:
        size, colour = [x.strip() for x in size.split(' / ', 1)]
    elif colours and not sizes and not colour and size:
        colour, size = size, ''
    s = match_size(sizes, size) if sizes else ''
    if sizes and not s:
        return '', 'size'
    c = match_size(colours, colour) if colours else ''
    if colours and not c:
        return '', 'colour'
    return ' / '.join(x for x in (s, c) if x), None


def match_size(sizes, wanted):
    wanted = str(wanted or '').strip().lower()
    return next((s for s in sizes if s.lower() == wanted), '')



# ---------------------------------------------------------------- website looks (6 customer-selectable layouts)
# 'menu' is where the menu button sits by default for that look (customers can override it: tl / tr / bl / br).
UI_LAYOUTS = {
    'classic': {
        'name': 'Classic', 'menu': 'tl',
        'tagline': 'Deep green, text menu on top, left-aligned banner.',
    },
    'modern': {
        'name': 'Modern App', 'menu': 'tr',
        'tagline': 'Indigo & coral, centred banner, tab bar along the bottom.',
        'copy': {
            'hero_eyebrow': 'NEW SEASON · SHREEJEE FASHION',
            'hero_heading': 'Wear it',
            'hero_emphasis': 'your way.',
            'hero_description': 'Fresh everyday looks and festive rentals. Pick your style, save your favourites and collect from our store.',
            'hero_primary_text': 'Start shopping',
            'hero_secondary_text': 'Our story',
            'quote': '\u201cFashion fades, style stays.\u201d',
        },
    },
    'boutique': {
        'name': 'Festive Boutique', 'menu': 'tr',
        'tagline': 'Maroon & gold, framed banner, centred logo with links below.',
        'copy': {
            'hero_eyebrow': 'FESTIVE & WEDDING COLLECTION',
            'hero_heading': 'Dressed for',
            'hero_emphasis': 'every celebration.',
            'hero_description': 'Chaniya choli, lehenga, anarkali and jewellery to buy or rent, with the warmth of a family store since 2021.',
            'hero_primary_text': 'Explore the collection',
            'hero_secondary_text': 'Visit the store',
            'quote': '\u201cEvery celebration deserves a story worth wearing.\u201d',
        },
    },
    'noir': {
        'name': 'Midnight Luxe', 'menu': 'tl',
        'tagline': 'Dark theme, champagne gold, buttons on a side rail.',
        'copy': {
            'hero_eyebrow': 'THE EVENING EDIT',
            'hero_heading': 'Quiet luxury,',
            'hero_emphasis': 'made to be worn.',
            'hero_description': 'Statement pieces and occasion rentals for the nights that matter. Handpicked, fitted and ready at our Dhanbad store.',
            'hero_primary_text': 'Shop the edit',
            'hero_secondary_text': 'Find the store',
            'quote': '\u201cStyle is a way to say who you are without speaking.\u201d',
        },
    },
    'fresh': {
        'name': 'Fresh Pop', 'menu': 'tl',
        'tagline': 'Bold mint, lemon & pink, sticker cards, floating buttons.',
        'copy': {
            'hero_eyebrow': 'NEW DROP · KIDS, BOYS & GIRLS',
            'hero_heading': 'Play. Twirl.',
            'hero_emphasis': 'Repeat!',
            'hero_description': 'Comfy, colourful outfits for growing families. Pick it, love it, wear it all week.',
            'hero_primary_text': 'See what\u2019s new',
            'hero_secondary_text': 'Say hello',
            'quote': '\u201cLife is short. Wear the fun outfit.\u201d',
        },
    },
    'editorial': {
        'name': 'Editorial', 'menu': 'tl',
        'tagline': 'Black & white magazine style, big type, menu in the corner.',
        'copy': {
            'hero_eyebrow': 'ISSUE 26 · THE FAMILY ISSUE',
            'hero_heading': 'Dress well,',
            'hero_emphasis': 'together.',
            'hero_description': 'A considered wardrobe for every generation, from first-day-of-school shirts to wedding-season lehengas.',
            'hero_primary_text': 'Read the collection',
            'hero_secondary_text': 'About the store',
            'quote': '\u201cThe best-dressed family is the one that is comfortable.\u201d',
        },
    },
}

def resolve_ui_layout(user, site_config=None):
    """Resolve a visitor's chosen theme, falling back to the admin-published site default."""
    config = site_config if isinstance(site_config, dict) else get_site_config()
    default_layout = str(config.get('default_ui', 'classic') or 'classic')
    if default_layout not in UI_LAYOUTS:
        default_layout = 'classic'
    cookie_value = request.cookies.get('sf_ui', '')
    if user and user['role'] == 'admin':
        # Admin cookie is a private preview preference; the published default is used on fresh browsers.
        if cookie_value in UI_LAYOUTS:
            return cookie_value, True
        return default_layout, False
    saved = ''
    if user:
        try: saved = user['ui_layout'] or ''
        except (KeyError, IndexError): saved = ''
    if saved in UI_LAYOUTS:
        return saved, True
    if cookie_value in UI_LAYOUTS:
        return cookie_value, True
    return default_layout, False


# Product-page buttons the owner can reorder (and hide, except Buy / Rent) from the Website Editor.
PRODUCT_BUTTONS = [
    ('buy', 'Buy this item', False),
    ('rent', 'Rent this item', False),
    ('cart', 'View cart', True),
    ('save', 'Save for later', True),
    ('whatsapp', 'Ask on WhatsApp', True),
    ('review', 'Review / feedback', True),
]
DEFAULT_BUTTON_ORDER = ','.join(k for k, _, _ in PRODUCT_BUTTONS)


def product_button_rows(config):
    """Ordered [{key,label,can_hide,visible}] for the product page, from the saved order/hidden lists."""
    meta = {k: (label, can_hide) for k, label, can_hide in PRODUCT_BUTTONS}
    wanted = [k.strip() for k in str(config.get('product_button_order') or '').split(',')]
    order = []
    for k in wanted + [k for k, _, _ in PRODUCT_BUTTONS]:
        if k in meta and k not in order:
            order.append(k)
    hidden = {k.strip() for k in str(config.get('product_button_hidden') or '').split(',')}
    return [{'key': k, 'label': meta[k][0], 'can_hide': meta[k][1], 'visible': not (meta[k][1] and k in hidden)} for k in order]


app.jinja_env.globals['product_button_rows'] = product_button_rows


def wa_link(text='', phone=None):
    """wa.me chat link. Defaults to the owner's WhatsApp number (8340453008)."""
    from urllib.parse import quote
    digits = re.sub(r'\D', '', phone if phone is not None else (get_site_config().get('phone') or '')) or re.sub(r'\D', '', OWNER_PHONE)
    if len(digits) == 10:
        digits = '91' + digits
    return 'https://wa.me/' + digits + ('?text=' + quote(text) if text else '')


def get_wishlist_ids(user):
    if not user or user['role'] != 'customer':
        return set()
    with db() as con:
        return {r['product_id'] for r in con.execute('SELECT product_id FROM wishlist_items WHERE user_id=?', (user['id'],)).fetchall()}


def order_return_lines(con, order):
    """Lines of a delivered order that can still be returned/exchanged -> (lines, closed_reason)."""
    if order['status'] not in DELIVERED_STATES:
        return [], 'Returns and exchanges open once the order has been delivered.'
    try:
        delivered = datetime.strptime(order['updated_at'] or order['created_at'], '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        delivered = datetime.now()
    if (datetime.now() - delivered).days > RETURN_WINDOW_DAYS:
        return [], f'The {RETURN_WINDOW_DAYS}-day return window for this order has closed. Please contact the store on WhatsApp.'
    asked = {r['line_key']: int(r['q'] or 0) for r in con.execute("SELECT line_key, SUM(qty) q FROM return_requests WHERE order_id=? AND status<>'Rejected' GROUP BY line_key", (order['id'],)).fetchall()}
    ratio = (float(order['total']) / float(order['subtotal'])) if float(order['subtotal'] or 0) > 0 else 1.0
    lines = []
    try:
        items = json.loads(order['items_json'])
    except (TypeError, json.JSONDecodeError):
        items = []
    for idx, it in enumerate(items):
        if (it.get('mode') or 'buy') != 'buy':
            continue  # rentals are returned to the store, not refunded
        key = f"{it.get('id')}:{it.get('size', '')}"
        remaining = int(it.get('qty', 0) or 0) - asked.get(key, 0)
        if remaining <= 0:
            continue
        prow = con.execute('SELECT sizes FROM products WHERE id=?', (it.get('id'),)).fetchone()
        unit = float(it.get('price', 0) or 0)
        lines.append({'idx': idx, 'key': key, 'id': it.get('id'), 'name': it.get('name', 'Item'), 'size': it.get('size', ''),
                      'remaining': remaining, 'unit_paid': round(unit * ratio, 2), 'sizes': parse_sizes(prow['sizes']) if prow else []})
    return lines, ('' if lines else 'Every item in this order already has a return or exchange request.')


def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    with db() as con:
        user = con.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    # Revoke an already-open customer session as soon as an admin blocks the account.
    if user and user['blocked']:
        session.pop('user_id', None)
        return None
    return user


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('login', next=request.path))
        if user['blocked']:
            session.clear()
            flash('This account is blocked. Please contact the store.', 'danger')
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or user['role'] != 'admin':
            flash('Please log in with an administrator account.', 'warning')
            return redirect(url_for('login', next=request.path))
        if user['blocked']:
            session.clear()
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper


def parse_cart_key(key):
    parts = str(key).split(':', 2)
    try:
        pid = int(parts[0])
    except (TypeError, ValueError):
        return None
    mode = parts[1].lower() if len(parts) > 1 else 'buy'
    if mode not in ('buy', 'rent'):
        mode = 'buy'
    size = parts[2][:60] if len(parts) > 2 else ''
    return pid, mode, size


def get_cart_dict(user=None):
    # Only customers get a shopping cart; admin sessions are always shop-disabled.
    if user is None:
        user = current_user()
    if user and user['role'] == 'admin':
        return {}
    if user and user['role'] == 'customer':
        with db() as con:
            rows = con.execute('SELECT cart_key,qty FROM user_cart_items WHERE user_id=?', (user['id'],)).fetchall()
        return {row['cart_key']: int(row['qty']) for row in rows if int(row['qty']) > 0}
    raw = session.get('cart', {})
    return dict(raw) if isinstance(raw, dict) else {}


def user_purchased_qty(con, user_id, product_id):
    """Units of a product this account has already ordered (cancelled orders do not count)."""
    total = 0
    rows = con.execute("SELECT items_json FROM orders WHERE user_id=? AND status<>'Cancelled'", (user_id,)).fetchall()
    for row in rows:
        try:
            items = json.loads(row['items_json'])
        except (TypeError, json.JSONDecodeError):
            continue
        for item in items:
            try:
                if int(item.get('id', 0)) == int(product_id):
                    total += max(0, int(item.get('qty', 0) or 0))
            except (TypeError, ValueError, AttributeError):
                continue
    return total


def purchase_allowance(con, user_id, product_id):
    """Return (limit, remaining_for_cart). limit 0 means the admin set no limit.

    remaining_for_cart already subtracts past orders and everything currently in this user's cart."""
    row = con.execute('SELECT max_per_user FROM products WHERE id=?', (int(product_id),)).fetchone()
    limit = int(row['max_per_user'] or 0) if row else 0
    if limit <= 0:
        return 0, None
    ordered = user_purchased_qty(con, user_id, product_id)
    in_cart = con.execute('SELECT COALESCE(SUM(qty),0) FROM user_cart_items WHERE user_id=? AND product_id=?', (user_id, int(product_id))).fetchone()[0]
    return limit, max(0, limit - ordered - int(in_cart))


def _add_cart_item(product_id, mode='buy', qty=1, size='', colour=''):
    user = current_user()
    if user and user['role'] == 'admin':
        return False, 'Admin accounts cannot add products to a cart.'
    mode = mode if mode in ('buy', 'rent') else 'buy'
    try: qty = max(1, min(20, int(qty)))
    except (TypeError, ValueError): qty = 1
    with db() as con:
        product = con.execute('SELECT id,name,offer,rental_price,active,sizes,colours FROM products WHERE id=?', (int(product_id),)).fetchone()
    if not product or not product['active']:
        return False, 'That product is no longer available.'
    size, missing = resolve_variant(product, size, colour)
    if missing:
        return False, f"Please choose a {missing} for {product['name']}."
    if mode == 'rent' and (product['rental_price'] is None or product['offer'] == 'buy'):
        mode = 'buy'
    if mode == 'buy' and product['offer'] == 'rent' and product['rental_price'] is not None:
        mode = 'rent'
    key = f"{int(product_id)}:{mode}" + (f":{size}" if size else '')
    user = current_user()
    limit_note = ''
    if user and user['role'] == 'customer':
        with db() as con:
            limit, remaining = purchase_allowance(con, user['id'], product_id)
            row = con.execute('SELECT qty FROM user_cart_items WHERE user_id=? AND cart_key=?', (user['id'], key)).fetchone()
            existing = int(row['qty']) if row else 0
            stock_row = con.execute('SELECT stock FROM products WHERE id=?', (int(product_id),)).fetchone()
            stock = stock_row['stock'] if stock_row else None
            in_cart_total = con.execute('SELECT COALESCE(SUM(qty),0) FROM user_cart_items WHERE user_id=? AND product_id=?', (user['id'], int(product_id))).fetchone()[0]
        if stock is not None:
            if int(stock) <= 0:
                return False, f"{product['name']} is sold out."
            room = int(stock) - int(in_cart_total)
            if room <= 0:
                return False, f"Only {int(stock)} of {product['name']} available, and they are already in your cart."
            if qty > room:
                qty = room
                limit_note += f" Only {int(stock)} available."
        if limit and remaining is not None:
            if remaining <= 0:
                return False, f"Purchase limit reached for {product['name']}: the store allows {limit} per customer."
            if qty > remaining:
                qty = remaining
                limit_note = f" Only {remaining} more allowed (limit {limit} per customer)."
        with db() as con:
            total_qty = min(existing + qty, 20)
            con.execute('''INSERT INTO user_cart_items(user_id,cart_key,product_id,mode,size,qty,updated_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(user_id,cart_key) DO UPDATE SET qty=excluded.qty,updated_at=excluded.updated_at''',
                (user['id'], key, int(product_id), mode, size, total_qty, now()))
        # Mirror to current session only for compatibility; database is the source of truth.
        cart = dict(session.get('cart', {})); cart[key] = total_qty; session['cart'] = cart
    else:
        cart = dict(session.get('cart', {}))
        try: existing = int(cart.get(key, 0))
        except (TypeError, ValueError): existing = 0
        cart[key] = min(existing + qty, 20)
        session['cart'] = cart
    session['checkout_key'] = secrets.token_urlsafe(24)
    return True, f"{product['name']} added to cart." + limit_note

def consume_pending_cart_add():
    pending = session.pop('pending_cart_add', None)
    user = current_user()
    if user and user['role'] == 'admin':
        session.pop('cart', None)
        session.pop('coupon_code', None)
        return url_for('admin_dashboard')
    if not isinstance(pending, dict):
        return None
    if time.time() - float(pending.get('created_at', 0) or 0) > 1800:
        return safe_local_path(pending.get('return_to'), url_for('home'))
    ok, message = _add_cart_item(pending.get('product_id'), pending.get('mode', 'buy'), pending.get('qty', 1), pending.get('size', ''))
    if ok:
        flash(message, 'success')
    else:
        flash(message, 'warning')
    return safe_local_path(pending.get('return_to'), url_for('home'))


def cart_data():
    user = current_user()
    if user and user['role'] == 'admin':
        return [], 0.0, 0
    raw = get_cart_dict(user)
    clean = {}
    for key, qty in raw.items():
        parsed = parse_cart_key(key)
        if not parsed:
            continue
        try: qty = int(qty)
        except (TypeError, ValueError): continue
        if qty > 0:
            pid, mode, size = parsed
            clean[(pid, mode, size)] = min(qty, 20)
    if not clean:
        return [], 0.0, 0
    with db() as con:
        ids = list({pid for pid, _, _ in clean})
        placeholders = ','.join('?' for _ in ids)
        rows = con.execute(f'SELECT * FROM products WHERE active=1 AND id IN ({placeholders})', ids).fetchall()
    by_id = {row['id']: row for row in rows}
    products, subtotal, count = [], 0.0, 0
    for (pid, mode, size), qty in clean.items():
        row = by_id.get(pid)
        if not row: continue
        if row['offer'] == 'rent' and row['rental_price'] is not None: mode = 'rent'
        elif row['offer'] == 'buy' or row['rental_price'] is None: mode = 'buy'
        unit = float(row['rental_price'] if mode == 'rent' else row['price'])
        line = round(unit * qty, 2)
        cart_key = f"{pid}:{mode}" + (f":{size}" if size else '')
        products.append({'id': row['id'], 'cart_key': cart_key, 'name': row['name'], 'category': row['category'], 'description': row['description'], 'price': unit, 'buy_price': float(row['price']), 'rental_price': row['rental_price'], 'offer': row['offer'], 'mode': mode, 'size': size, 'image': row['image'], 'qty': qty, 'line_total': line, 'max_per_user': int(row['max_per_user'] or 0)})
        subtotal += line
        count += qty
    return products, round(subtotal, 2), count


def _order_number_from_text(title, body):
    match = re.search(r'\border\s*#\s*(\d+)\b', f'{title} {body}', re.I)
    return int(match.group(1)) if match else None


def resolve_notification_actions(rows, user):
    """Give notifications a useful destination instead of making customers only expand text.

    Once the linked order has been delivered there is nothing left to act on, so the button is removed."""
    is_admin = bool(user and user['role'] == 'admin')
    resolved = []
    with db() as con:
        for row in rows:
            item = dict(row)
            action_url = item.get('action_url') or ''
            order_id = _order_number_from_text(item.get('title', ''), item.get('body', ''))
            linked = None
            if order_id:
                if is_admin:
                    linked = con.execute('SELECT id,token,status FROM orders WHERE id=?', (order_id,)).fetchone()
                elif user:
                    linked = con.execute('SELECT id,token,status FROM orders WHERE id=? AND user_id=?', (order_id, user['id'])).fetchone()
            elif action_url.startswith('/order/'):
                token = action_url.split('/order/', 1)[1].split('?', 1)[0]
                linked = con.execute('SELECT id,token,status FROM orders WHERE token=?', (token,)).fetchone()
            if not action_url:
                if order_id and is_admin:
                    action_url = f'/admin/orders/{order_id}'
                elif order_id and user:
                    action_url = f'/order/{linked["token"]}' if linked else '/account'
                else:
                    action_url = '/admin/orders' if is_admin else '/account'
            # Only allow local relative paths in notification destinations.
            item['action_url'] = safe_local_path(action_url, '/admin/orders' if is_admin else '/account')
            if '/order/' in item['action_url']:
                item['action_label'] = 'Open order and choose next step →'
            elif is_admin:
                item['action_label'] = 'Review in admin →'
            elif item['action_url'].startswith('/?') or item['action_url'] == '/':
                item['action_label'] = 'Explore styles →'
            else:
                item['action_label'] = 'Open account →'
            if linked and linked['status'] in DELIVERED_STATES:
                item['action_url'] = ''
                item['action_label'] = ''
            resolved.append(item)
    return resolved


def notify_admins(title, body):
    order_id = _order_number_from_text(title, body)
    action_url = f'/admin/orders/{order_id}' if order_id else '/admin/orders'
    with db() as con:
        admins = con.execute("SELECT id FROM users WHERE role='admin' AND blocked=0").fetchall()
        for admin in admins:
            con.execute('INSERT INTO notifications(user_id,title,body,action_url,created_at) VALUES(?,?,?,?,?)', (admin['id'], title, body, action_url, now()))


def notify_users(title, body):
    action_url = '/?browse=1'
    with db() as con:
        users = con.execute("SELECT id FROM users WHERE role='customer' AND blocked=0").fetchall()
        for user in users:
            con.execute('INSERT INTO notifications(user_id,title,body,action_url,created_at) VALUES(?,?,?,?,?)', (user['id'], title, body, action_url, now()))


def notify_one(user_id, title, body, action_url=None):
    if not user_id:
        return
    if not action_url:
        order_id = _order_number_from_text(title, body)
        if order_id:
            with db() as con:
                found = con.execute('SELECT token FROM orders WHERE id=? AND user_id=?', (order_id, user_id)).fetchone()
            action_url = f'/order/{found["token"]}' if found else '/account'
        else:
            action_url = '/account'
    action_url = safe_local_path(action_url, '/account')
    with db() as con:
        con.execute('INSERT INTO notifications(user_id,title,body,action_url,created_at) VALUES(?,?,?,?,?)', (user_id, title, body, action_url, now()))


def get_order_by_token(token):
    with db() as con:
        return con.execute('SELECT o.*, u.name AS account_name FROM orders o LEFT JOIN users u ON u.id=o.user_id WHERE o.token=?', (token,)).fetchone()


def order_items(order):
    try:
        return json.loads(order['items_json'])
    except (TypeError, json.JSONDecodeError):
        return []


def get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.before_request
def protect_form_posts():
    if request.method == 'POST' and request.endpoint != 'verify_payment':
        sent = request.form.get('csrf_token', '')
        expected = session.get('_csrf_token', '')
        if not sent or not expected or not secrets.compare_digest(sent, expected):
            abort(400, description='This form expired or failed its security check. Reload the page and try again.')


@app.context_processor
def inject_globals():
    user = current_user()
    _, _, cart_count = cart_data()
    unread = 0
    if user:
        with db() as con:
            unread = con.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0', (user['id'],)).fetchone()[0]
    site_config = get_site_config()
    announcement = (site_config.get('announcement') or '').strip()  # empty = no green bar at all
    wishlist_ids = get_wishlist_ids(user)
    pending_returns = 0
    admin_order_snapshot = ''
    if user and user['role'] == 'admin':
        with db() as con:
            pending_returns = con.execute("SELECT COUNT(*) FROM return_requests WHERE status='Requested'").fetchone()[0]
            # Snapshot every admin page so an order placed/updated while the admin is elsewhere
            # can trigger a refresh of that page too, not just Dashboard and Orders.
            admin_order_snapshot = order_change_snapshot(con)
    show_announcement = not (request.path == '/cart' or (request.path == '/' and (request.args.get('category') or request.args.get('view') or request.args.get('browse') or request.args.get('q'))))
    store = {'name': site_config.get('site_name') or STORE['name'], 'owner': site_config.get('owner') or STORE['owner'], 'address': site_config.get('address') or STORE['address'], 'since': site_config.get('since') or STORE['since'], 'phone': site_config.get('phone') or OWNER_PHONE, 'hours': site_config.get('hours','')}
    css_vars = ('--ink:'+site_config['text_color']+';--green:'+site_config['primary_color']+';--green2:'+site_config['primary_color']+';--orange:'+site_config['accent_color']+';--cream:'+site_config['page_background']+';--paper:'+site_config['card_background']+';--sans:'+('"'+site_config['font_family']+'",system-ui,sans-serif')+';--button-radius:'+str(site_config['button_radius'])+'px;--card-radius:'+str(site_config['card_radius'])+'px;--card-gap:'+str(site_config['card_gap'])+'px;--base-font-size:'+str(site_config['base_font_size'])+'px;--heading-scale:'+str(site_config['heading_scale'])+';')
    is_null_customer = bool(user and user['role'] == 'customer' and str(user['email']).lower() == DEMO_CUSTOMER_EMAIL)
    ui_layout, ui_chosen = resolve_ui_layout(user, site_config)
    display_user = user
    # Menu placement is part of the chosen look now; users no longer see a separate move-menu control.
    menu_choice = ''
    menu_pos = UI_LAYOUTS[ui_layout]['menu']
    menu_side = 'right' if menu_pos.endswith('r') else 'left'
    path = request.path
    on_plain_home = path == '/' and not any(request.args.get(k) for k in ('category', 'view', 'browse', 'q'))
    back_fallback = url_for('admin_dashboard') if (path.startswith('/admin/') and path != '/admin/') else url_for('home')
    if ui_layout == 'classic':
        ui_copy = {k: site_config.get(k, '') for k in ('hero_eyebrow', 'hero_heading', 'hero_emphasis', 'hero_description', 'hero_primary_text', 'hero_secondary_text')}
        ui_copy['quote'] = ''
    else:
        ui_copy = UI_LAYOUTS[ui_layout]['copy']
    pending_resets = 0
    if user and user['role'] == 'admin':
        with db() as con:
            pending_resets = con.execute("SELECT COUNT(*) FROM password_resets WHERE status='Requested'").fetchone()[0]
    return {'hide_taskbar': request.endpoint in ('login', 'login_otp', 'register', 'forgot_password', 'reset_password'), 'menu_pos': menu_pos, 'menu_choice': menu_choice, 'show_back': not on_plain_home, 'back_fallback': back_fallback, 'ui_layout': ui_layout, 'ui_chosen': ui_chosen, 'ui_copy': ui_copy, 'ui_layouts': UI_LAYOUTS, 'pending_resets': pending_resets, 'admin_order_snapshot': admin_order_snapshot, **_base_globals(display_user, site_config, announcement, store, css_vars, is_null_customer, cart_count, unread, wishlist_ids, menu_side, pending_returns, show_announcement)}


def _base_globals(user, site_config, announcement, store, css_vars, is_null_customer, cart_count, unread, wishlist_ids, menu_side, pending_returns, show_announcement):
    return {'store': store, 'site_config': site_config, 'site_css_vars': css_vars, 'current_user': user, 'is_null_customer': is_null_customer, 'cart_count': cart_count, 'unread_notifications': unread, 'announcement': announcement, 'show_announcement': show_announcement, 'csrf_token': get_csrf_token, 'wa_link': wa_link, 'wishlist_ids': wishlist_ids, 'menu_side': menu_side, 'pending_returns': pending_returns, 'menu_categories': sanitize_menu_categories(site_config.get('menu_categories', []))}


SORT_OPTIONS = [('new', 'Newest first'), ('price_asc', 'Price: low to high'), ('price_desc', 'Price: high to low'), ('name', 'Name: A to Z')]


def catalog_opts():
    args = request.args
    sort = args.get('sort', 'new')
    if sort not in dict(SORT_OPTIONS): sort = 'new'
    def num(key):
        try:
            value = float(args.get(key, '').strip())
            return value if value >= 0 else None
        except (TypeError, ValueError):
            return None
    return {'sort': sort, 'min_price': num('min_price'), 'max_price': num('max_price'), 'size': args.get('size', '').strip()[:24], 'colour': args.get('colour', '').strip()[:24], 'in_stock': args.get('in_stock') == '1'}


def catalog_filter_args(opts):
    out = {}
    if opts['sort'] != 'new': out['sort'] = opts['sort']
    if opts['min_price'] is not None: out['min_price'] = int(opts['min_price']) if float(opts['min_price']).is_integer() else opts['min_price']
    if opts['max_price'] is not None: out['max_price'] = int(opts['max_price']) if float(opts['max_price']).is_integer() else opts['max_price']
    if opts['size']: out['size'] = opts['size']
    if opts['colour']: out['colour'] = opts['colour']
    if opts['in_stock']: out['in_stock'] = '1'
    return out


def catalog_choice_lists(products):
    sizes, colours, seen_s, seen_c = [], [], set(), set()
    for p in products:
        for s in parse_sizes(p['sizes']):
            if s.lower() not in seen_s: seen_s.add(s.lower()); sizes.append(s)
        for c in parse_colours(p['colours']):
            if c.lower() not in seen_c: seen_c.add(c.lower()); colours.append(c)
    return sizes, colours


def apply_catalog_opts(products, opts, view='all'):
    def price_of(p):
        rent_first = view == 'rent' or p['offer'] == 'rent'
        return float(p['rental_price'] if rent_first and p['rental_price'] else p['price'])
    out = []
    for p in products:
        price = price_of(p)
        if opts['min_price'] is not None and price < opts['min_price']: continue
        if opts['max_price'] is not None and price > opts['max_price']: continue
        if opts['size'] and opts['size'].lower() not in [s.lower() for s in parse_sizes(p['sizes'])]: continue
        if opts['colour'] and opts['colour'].lower() not in [c.lower() for c in parse_colours(p['colours'])]: continue
        if opts['in_stock'] and p['stock'] is not None and int(p['stock']) <= 0: continue
        out.append(p)
    if opts['sort'] == 'price_asc': out.sort(key=price_of)
    elif opts['sort'] == 'price_desc': out.sort(key=price_of, reverse=True)
    elif opts['sort'] == 'name': out.sort(key=lambda p: p['name'].lower())
    return out


def get_catalog_rows(category='', search='', view='all'):
    sql = 'SELECT * FROM products WHERE active=1'
    params = []
    with db() as con:
        categories = [r['category'] for r in con.execute('SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category').fetchall()]
        if category:
            sql += ' AND category=?'; params.append(category)
        if view == 'buy': sql += " AND offer IN ('buy','both')"
        elif view == 'rent': sql += " AND offer IN ('rent','both') AND rental_price IS NOT NULL"
        if search:
            sql += ' AND (name LIKE ? OR description LIKE ?)'; params.extend([f'%{search}%', f'%{search}%'])
        sql += ' ORDER BY id DESC'
        products = con.execute(sql, params).fetchall()
    return products, categories


@app.route('/')
def home():
    category = request.args.get('category', '').strip()
    search = request.args.get('q', '').strip()
    view = request.args.get('view', 'all').strip().lower()
    if view not in ('all', 'buy', 'rent'): view = 'all'
    user = current_user()
    site_config = get_site_config()
    category_locked = bool(customer_category_locked(category, site_config) and not (user and user['role'] == 'admin'))
    products, categories = get_catalog_rows(category, search, view)
    if not (user and user['role'] == 'admin'):
        locked_names = locked_menu_category_names(site_config)
        products = [product for product in products if product['category'] not in locked_names]
        if category_locked:
            products = []
    return_to = safe_local_path(request.full_path, url_for('home', browse='1'))
    order = None; last_order_items = []
    if user and user['role'] != 'admin':
        with db() as con:
            order = con.execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1', (user['id'],)).fetchone()
        if order:
            order = dict(order)
            last_order_items = order_items(order)
    ordered_categories = [x.strip() for x in site_config.get('category_order','').split(',') if x.strip()]
    categories = sorted(categories, key=lambda x: (ordered_categories.index(x) if x in ordered_categories else len(ordered_categories), x.lower()))
    if not (user and user['role'] == 'admin'):
        locked_names = {item['category'] for item in sanitize_menu_categories(site_config.get('menu_categories', [])) if item.get('type') == 'coming_soon'}
        categories = [name for name in categories if name not in locked_names]
    opts = catalog_opts()
    size_choices, colour_choices = catalog_choice_lists(products)
    products = apply_catalog_opts(products, opts, view)
    filter_args = catalog_filter_args(opts)
    is_homepage = not category and not search and view == 'all' and not request.args.get('browse') and not filter_args
    return render_template('home.html', opts=opts, filter_args=filter_args, filters_active=bool(filter_args), size_choices=size_choices, colour_choices=colour_choices, sort_options=SORT_OPTIONS, products=products, selected_category=category, search=search, categories=categories, view=view, is_homepage=is_homepage, last_order=order, last_order_items=last_order_items, return_to=return_to, category_locked=category_locked)


@app.route('/api/catalog-fragment')
def catalog_fragment():
    category = request.args.get('category', '').strip()
    search = request.args.get('q', '').strip()
    view = request.args.get('view', 'all').strip().lower()
    if view not in ('all', 'buy', 'rent'): view = 'all'
    products, _categories = get_catalog_rows(category, search, view)
    user = current_user()
    if not (user and user['role'] == 'admin'):
        locked_names = locked_menu_category_names()
        products = [product for product in products if product['category'] not in locked_names]
        if customer_category_locked(category):
            products = []
    products = apply_catalog_opts(products, catalog_opts(), view)
    fallback = url_for('home', browse='1' if (not category and view == 'all') else None, category=category or None, q=search or None, view=view if view != 'all' else None)
    return_to = safe_local_path(request.args.get('return_to'), fallback)
    return render_template('_catalog_results.html', products=products, view=view, return_to=return_to)


@app.route('/api/product/<int:product_id>')
def api_product(product_id):
    with db() as con:
        product = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
    if not product or not product['active']:
        return jsonify({'ok': True, 'active': False, 'id': product_id})
    user = current_user()
    if customer_category_locked(product['category']) and not (user and user['role'] == 'admin'):
        return jsonify({'ok': True, 'active': False, 'id': product_id})
    return jsonify({'ok': True, 'active': True, 'id': product['id'], 'name': product['name'], 'category': product['category'], 'description': product['description'], 'price': product['price'], 'rental_price': product['rental_price'], 'offer': product['offer'], 'max_per_user': int(product['max_per_user'] or 0), 'stock': product['stock'], 'image': product['image'], 'images': product_image_list(product['id'], product['image']), 'updated_at': product['updated_at'] or product['created_at']})


@app.route('/api/categories')
def api_categories():
    with db() as con:
        categories = [r['category'] for r in con.execute('SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category').fetchall()]
    config = get_site_config()
    ordering = [x.strip() for x in config.get('category_order','').split(',') if x.strip()]
    categories = sorted(categories, key=lambda x: (ordering.index(x) if x in ordering else len(ordering), x.lower()))
    user = current_user()
    if not (user and user['role'] == 'admin'):
        locked_names = {item['category'] for item in sanitize_menu_categories(config.get('menu_categories', [])) if item.get('type') == 'coming_soon'}
        categories = [name for name in categories if name not in locked_names]
    return jsonify({'categories': categories, 'view': request.args.get('view','all'), 'category_order': ordering})


@app.route('/api/site-settings')
def api_site_settings():
    config = get_site_config()
    # Public display values only; no admin data or secrets are exposed.
    return jsonify({k: config.get(k) for k in SITE_EDITOR_DEFAULTS.keys() if k not in ('category_order',)})


@app.route('/api/order-status/<token>')
def api_order_status(token):
    order = get_order_by_token(token)
    if not order:
        return jsonify({'ok': False, 'message': 'Order not found.'}), 404
    user = current_user()
    if order['user_id'] and (not user or (user['role'] != 'admin' and user['id'] != order['user_id'])):
        return jsonify({'ok': False, 'message': 'Not authorized to view this order.'}), 403
    is_owner_customer = bool(user and user['role'] != 'admin' and order['user_id'] == user['id'])
    customer_code = (order['confirmation_code'] or '') if is_owner_customer and order['status'] == 'Awaiting delivery code' and not order['confirmation_code_used'] else ''
    return jsonify({'ok': True, 'id': order['id'], 'status': order['status'], 'payment_status': order['payment_status'],
                    'confirmation_code': customer_code,
                    'confirmation_code_used': bool(order['confirmation_code_used']),
                    'payment_method': order['payment_method'],
                    'total': float(order['total'] or 0),
                    'subtotal': float(order['subtotal'] or 0),
                    'discount': float(order['discount'] or 0),
                    'delivery_charge': float(order['delivery_charge'] or order['cod_extra_charge'] or 0),
                    'delivery_charge_status': order['delivery_charge_status'] or order['cod_charge_status'] or '',
                    'eta': format_eta(order),
                    'updated_at': order['updated_at'] or order['created_at']})


def available_categories():
    with db() as con:
        existing = [r['category'] for r in con.execute('SELECT DISTINCT category FROM products ORDER BY category').fetchall()]
    return list(dict.fromkeys(CATEGORIES + existing))


def product_image_list(product_id, primary_image=''):
    with db() as con:
        rows = con.execute('SELECT filename FROM product_images WHERE product_id=? ORDER BY position,id', (product_id,)).fetchall()
    values = [row['filename'] for row in rows if row['filename']]
    if primary_image:
        values = [primary_image] + [value for value in values if value != primary_image]
    return list(dict.fromkeys(values))


def save_product_images(files):
    saved = []
    try:
        for item in (files or []):
            if item and item.filename:
                if len(saved) >= 8:
                    raise ValueError('Please upload no more than 8 images per product.')
                saved_name = save_image(item)
                if saved_name:
                    saved.append(saved_name)
    except Exception:
        for filename in saved:
            try: os.remove(os.path.join(UPLOAD_DIR, filename))
            except OSError: pass
        raise
    return saved


def attach_product_images(con, product_id, filenames, start_position=0):
    for offset, filename in enumerate(filenames):
        con.execute('INSERT INTO product_images(product_id,filename,position,created_at) VALUES(?,?,?,?)',
                    (product_id, filename, start_position + offset, now()))


@app.route('/product/<int:product_id>')
def product_detail(product_id):
    with db() as con:
        product = con.execute('SELECT * FROM products WHERE id=? AND active=1', (product_id,)).fetchone()
        reviews = con.execute('SELECT r.*,u.name AS reviewer_name FROM product_reviews r JOIN users u ON u.id=r.user_id WHERE r.product_id=? ORDER BY r.updated_at DESC LIMIT 100', (product_id,)).fetchall()
        own_review = None
        user = current_user()
        if user and user['role'] == 'customer':
            own_review = con.execute('SELECT * FROM product_reviews WHERE product_id=? AND user_id=?', (product_id, user['id'])).fetchone()
    if not product: abort(404)
    if customer_category_locked(product['category']) and not (user and user['role'] == 'admin'):
        abort(404)
    images = product_image_list(product['id'], product['image'])
    return render_template('product_detail.html', product=product, images=images, reviews=reviews, own_review=own_review, sizes=parse_sizes(product['sizes']), colours=parse_colours(product['colours']))


@app.route('/product/<int:product_id>/review', methods=['POST'])
@login_required
def product_review(product_id):
    user = current_user()
    if not user or user['role'] != 'customer': abort(403)
    with db() as con:
        product = con.execute('SELECT id,category FROM products WHERE id=? AND active=1', (product_id,)).fetchone()
        if not product: abort(404)
        if customer_category_locked(product['category']):
            abort(404)
        try:
            rating = int(request.form.get('rating', '5'))
        except (TypeError, ValueError):
            rating = 0
        review_text = request.form.get('review_text', '').strip()[:1200]
        if rating < 1 or rating > 5 or not review_text:
            flash('Choose a 1–5 rating and enter a short review.', 'warning')
            return redirect(url_for('product_detail', product_id=product_id) + '#reviews')
        con.execute('''INSERT INTO product_reviews(product_id,user_id,rating,review_text,created_at,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(product_id,user_id) DO UPDATE SET rating=excluded.rating,review_text=excluded.review_text,updated_at=excluded.updated_at''',
            (product_id, user['id'], rating, review_text, now(), now()))
    flash('Thanks for sharing your feedback.', 'success')
    return redirect(url_for('product_detail', product_id=product_id) + '#reviews')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        if not name or not email or len(password) < 8:
            flash('Please enter your name, a valid email, and a password of at least 8 characters.', 'danger')
            return render_template('register.html')
        try:
            with db() as con:
                con.execute('INSERT INTO users(name,email,phone,password_hash,role,verified,blocked,created_at) VALUES(?,?,?,?,?,?,?,?)',
                            (name, email, phone, generate_password_hash(password), 'customer', 0, 0, now()))
                new_id = con.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()['id']
            session['user_id'] = new_id
            flash('Account created. An admin can verify your account from the dashboard.', 'success')
            pending_target = consume_pending_cart_add()
            target = pending_target or safe_local_path(request.args.get('next') or request.form.get('next'), url_for('home'))
            return redirect(target)
        except sqlite3.IntegrityError:
            flash('That email is already registered. Please log in.', 'warning')
    return render_template('register.html')


def complete_login(user, welcome='Welcome back!'):
    """Shared by password, Google and phone-OTP sign-in: start the session and carry the guest cart over."""
    is_customer_login = user['role'] == 'customer'
    saved_cart = session.get('cart') if is_customer_login else None
    saved_checkout_key = session.get('checkout_key') if is_customer_login else None
    pending_cart_add = session.get('pending_cart_add') if is_customer_login else None
    csrf = session.get('_csrf_token')
    next_hint = request.args.get('next') or request.form.get('next')
    session.clear()
    if saved_cart and is_customer_login: session['cart'] = saved_cart
    if saved_checkout_key and saved_cart and is_customer_login: session['checkout_key'] = saved_checkout_key
    if pending_cart_add and is_customer_login: session['pending_cart_add'] = pending_cart_add
    if csrf: session['_csrf_token'] = csrf
    session['user_id'] = user['id']
    if user['role'] == 'customer' and saved_cart:
        with db() as con:
            for cart_key, cart_qty in saved_cart.items():
                parsed = parse_cart_key(cart_key)
                if not parsed: continue
                pid, mode, size = parsed
                try: qty_value = max(1, min(20, int(cart_qty)))
                except (TypeError, ValueError): continue
                product = con.execute('SELECT id,active FROM products WHERE id=?', (pid,)).fetchone()
                if not product or not product['active']: continue
                existing = con.execute('SELECT qty FROM user_cart_items WHERE user_id=? AND cart_key=?', (user['id'], cart_key)).fetchone()
                if existing:
                    qty_value = max(int(existing['qty']), qty_value)
                con.execute('''INSERT INTO user_cart_items(user_id,cart_key,product_id,mode,size,qty,updated_at) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(user_id,cart_key) DO UPDATE SET qty=excluded.qty,updated_at=excluded.updated_at''',
                    (user['id'], cart_key, pid, mode, size, qty_value, now()))
        session['cart'] = {}
    flash(welcome, 'success')
    pending_target = consume_pending_cart_add() if user['role'] == 'customer' else None
    next_url = pending_target or safe_local_path(next_hint, '')
    if next_url and next_url != '/login': return redirect(next_url)
    return redirect(url_for('admin_dashboard') if user['role'] == 'admin' else url_for('home'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identity = request.form.get('identity', '').strip().lower()
        if identity == 'null':
            identity = 'default.customer@shreejee.local'
        password = request.form.get('password', '')
        wait = attempts_locked('login', identity)
        if wait:
            flash(f'Too many failed log-in attempts. Please try again in {wait} minute(s).', 'danger')
            return render_template('login.html', next_url=safe_local_path(request.args.get('next'), '')), 429
        with db() as con:
            user = con.execute('SELECT * FROM users WHERE lower(email)=? OR lower(name)=?', (identity, identity)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            if user['blocked']:
                flash('This account has been blocked. Contact the store administrator.', 'danger')
            else:
                attempts_clear('login', identity)
                return complete_login(user)
        else:
            attempts_record('login', identity)
            left = max(0, LOGIN_MAX_FAILS - attempts_count('login', identity))
            flash('Login details were not recognised.' + (f' {left} attempt(s) left before a {LOGIN_WINDOW_MIN}-minute lock.' if left <= 2 else ''), 'danger')
    return render_template('login.html', next_url=safe_local_path(request.args.get('next'), ''))


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))


@app.route('/account')
@login_required
def account():
    user = current_user()
    with db() as con:
        orders = con.execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC', (user['id'],)).fetchall()
        notifications = con.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 8', (user['id'],)).fetchall()
        returns = con.execute('SELECT r.*, o.token AS order_token FROM return_requests r JOIN orders o ON o.id=r.order_id WHERE r.user_id=? ORDER BY r.id DESC LIMIT 20', (user['id'],)).fetchall() if user['role'] == 'customer' else []
    notifications = resolve_notification_actions(notifications, user)
    return render_template('account.html', user=user, orders=orders, notifications=notifications, returns=returns)


def remove_uploaded_file(filename):
    filename = os.path.basename(filename or '')
    if filename:
        try: os.remove(os.path.join(UPLOAD_DIR, filename))
        except OSError: pass


@app.route('/account/profile', methods=['POST'])
@login_required
def update_profile():
    user = current_user()
    if str(user['email']).lower() == DEMO_CUSTOMER_EMAIL:
        flash('This shared account\'s details are locked.', 'warning')
        return redirect(url_for('account'))
    name = re.sub(r'\s+', ' ', request.form.get('name', '')).strip()
    phone = request.form.get('phone', '').strip()
    address = request.form.get('address', '').strip()
    if len(name) < 2 or len(name) > 80:
        flash('Please enter your name (2–80 characters).', 'danger'); return redirect(url_for('account') + '#profile')
    if phone and not re.fullmatch(r'[0-9+()\-\s]{8,20}', phone):
        flash('Please enter a valid phone number.', 'danger'); return redirect(url_for('account') + '#profile')
    if len(address) > 300:
        flash('Please keep the address under 300 characters.', 'danger'); return redirect(url_for('account') + '#profile')
    with db() as con:
        # Names can be used to log in, so never let one account take another account's login identity.
        clash = con.execute('SELECT 1 FROM users WHERE id<>? AND (lower(email)=? OR (role=\'admin\' AND lower(name)=?))', (user['id'], name.lower(), name.lower())).fetchone()
    if clash or name.lower() == 'null':
        flash('That name is reserved. Please choose another.', 'danger'); return redirect(url_for('account') + '#profile')
    new_image = ''
    upload = request.files.get('profile_image')
    if upload and upload.filename:
        try:
            new_image = save_image(upload)
        except ValueError as exc:
            flash(str(exc), 'danger'); return redirect(url_for('account') + '#profile')
    remove_photo = request.form.get('remove_photo') == '1'
    old_image = user['profile_image'] if 'profile_image' in user.keys() else ''
    image_value = new_image or ('' if remove_photo else old_image)
    with db() as con:
        con.execute('UPDATE users SET name=?, phone=?, address=?, profile_image=? WHERE id=?', (name, phone, address, image_value, user['id']))
    if old_image and image_value != old_image:
        remove_uploaded_file(old_image)
    flash('Your profile has been updated.', 'success')
    return redirect(url_for('account'))


@app.route('/cart')
def cart():
    user = current_user()
    if user and user['role'] == 'admin':
        session.pop('cart', None)
        session.pop('coupon_code', None)
        session.pop('pending_cart_add', None)
    items, subtotal, count = cart_data()
    # Reuse the same key for QR and COD submissions to stop double clicks creating duplicate orders.
    if items and not session.get('checkout_key'):
        session['checkout_key'] = secrets.token_urlsafe(24)
    if not items:
        session.pop('checkout_key', None)
    coupon = None; discount = 0.0
    code = session.get('coupon_code', '')
    if code:
        with db() as con: coupon = con.execute('SELECT * FROM coupons WHERE code=? AND active=1', (code,)).fetchone()
        if coupon and subtotal >= float(coupon['min_order']) and (not coupon['max_uses'] or coupon['used_count'] < coupon['max_uses']): discount = min(float(coupon['discount_amount']), subtotal)
        else: session.pop('coupon_code', None); code = ''; coupon = None
    # The cart keeps the customer's active orders visible even after checkout empties the basket.
    order_rows = []
    user = current_user()
    tokens = session.get('recent_order_tokens', [])
    with db() as con:
        if user and user['role'] != 'admin':
            order_rows.extend(con.execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 15', (user['id'],)).fetchall())
        safe_tokens = [t for t in tokens[-15:] if isinstance(t, str) and len(t) >= 12]
        if safe_tokens:
            placeholders = ','.join('?' for _ in safe_tokens)
            order_rows.extend(con.execute(f'SELECT * FROM orders WHERE token IN ({placeholders})', safe_tokens).fetchall())
    unique = {row['token']: row for row in order_rows}
    terminal = {'Completed', 'Delivered', 'Cancelled'}
    ongoing_orders = []
    for row in sorted(unique.values(), key=lambda item: item['id'], reverse=True):
        if row['status'] not in terminal:
            order_view = dict(row)
            order_view['items'] = order_items(row)
            ongoing_orders.append(order_view)
    if user and user['role'] == 'customer':
        with db() as con:
            for entry in items:
                if entry.get('max_per_user'):
                    entry['already_ordered'] = user_purchased_qty(con, user['id'], entry['id'])
    return render_template('cart.html', items=items, subtotal=subtotal, count=count, coupon=coupon, coupon_code=code, discount=discount, total=max(0, round(subtotal-discount,2)), ongoing_orders=ongoing_orders, saved_address=(user['address'] if user and user['address'] else session.get('saved_address','')), saved_phone=(user['phone'] if user and user['phone'] else session.get('saved_phone','')))


@app.route('/cart/add/<int:product_id>', methods=['POST'])
def add_to_cart(product_id):
    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json'
    mode = request.form.get('mode', request.args.get('mode', 'buy')).lower()
    if mode not in ('buy', 'rent'): mode = 'buy'
    return_to = safe_local_path(request.form.get('return_to') or request.referrer, url_for('home', browse='1'))
    user = current_user()
    if user and user['role'] == 'admin':
        message = 'Admin accounts cannot add products to a cart.'
        if wants_json: return jsonify({'ok': False, 'message': message}), 403
        flash(message, 'warning')
        return redirect(url_for('admin_dashboard'))
    try: qty = max(1, min(20, int(request.form.get('quantity', '1'))))
    except (TypeError, ValueError): qty = 1
    size = request.form.get('size', '').strip()[:30]
    colour = request.form.get('colour', '').strip()[:30]
    with db() as con:
        product = con.execute('SELECT id,name,offer,rental_price,active,sizes,colours,category FROM products WHERE id=? AND active=1', (product_id,)).fetchone()
    if not product:
        if wants_json: return jsonify({'ok': False, 'message': 'That product is no longer available.'}), 404
        flash('That product is no longer available.', 'warning')
        return redirect(return_to)
    if customer_category_locked(product['category']) and not (user and user['role'] == 'admin'):
        if wants_json: return jsonify({'ok': False, 'message': 'This collection is coming soon.'}), 403
        flash('This collection is coming soon.', 'info')
        return redirect(return_to)
    size, missing = resolve_variant(product, size, colour)
    if missing:
        if wants_json: return jsonify({'ok': False, 'needs_size': True, 'need': missing, 'sizes': parse_sizes(product['sizes']), 'colours': parse_colours(product['colours']), 'product_name': product['name'], 'message': f'Please choose a {missing}.'}), 422
        flash(f'Please choose a {missing} first.', 'warning')
        return redirect(url_for('product_detail', product_id=product_id))
    if not user:
        session['pending_cart_add'] = {'product_id': product_id, 'mode': mode, 'qty': qty, 'size': size, 'return_to': return_to, 'created_at': time.time()}
        login_url = url_for('login', next=return_to)
        register_url = url_for('register', next=return_to)
        if wants_json:
            return jsonify({'ok': False, 'auth_required': True, 'message': 'Log in or create an account to add this item.', 'login_url': login_url, 'register_url': register_url}), 401
        flash('Please log in or register. Your selected item will be added after login.', 'info')
        return redirect(login_url)
    ok, message = _add_cart_item(product_id, mode, qty, size)
    if not ok:
        if wants_json: return jsonify({'ok': False, 'message': message}), (404 if 'no longer available' in message else 409)
        flash(message, 'warning'); return redirect(return_to)
    if wants_json:
        _, _, cart_count = cart_data()
        return jsonify({'ok': True, 'message': message, 'cart_count': cart_count, 'product_id': product_id, 'mode': mode})
    flash(message, 'success')
    return redirect(return_to)


@app.route('/cart/update', methods=['POST'])
def update_cart():
    user = current_user()
    if user and user['role'] == 'admin':
        session.pop('cart', None); session.pop('coupon_code', None)
        flash('Admin accounts do not use a shopping cart.', 'warning')
        return redirect(url_for('admin_dashboard'))
    cart = get_cart_dict(user)
    for key in list(cart.keys()):
        try:
            safe_key = str(key).replace(':', '_')
            qty = int(request.form.get(f'qty_{safe_key}', cart[key]))
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            cart.pop(key, None)
        else:
            cart[key] = min(qty, 20)
    limited_products = []
    if user and user['role'] == 'customer':
        with db() as con:
            keys_by_product = {}
            for key in cart:
                parsed = parse_cart_key(key)
                if parsed:
                    keys_by_product.setdefault(parsed[0], []).append(key)
            for pid, keys in keys_by_product.items():
                row = con.execute('SELECT name,max_per_user,stock FROM products WHERE id=?', (pid,)).fetchone()
                if not row:
                    continue
                caps = []
                if int(row['max_per_user'] or 0) > 0:
                    caps.append(max(0, int(row['max_per_user']) - user_purchased_qty(con, user['id'], pid)))
                if row['stock'] is not None:
                    caps.append(max(0, int(row['stock'])))
                if not caps:
                    continue
                allowed = min(caps)
                over = sum(cart[k] for k in keys) - allowed
                if over <= 0:
                    continue
                limited_products.append(f"{row['name']} (only {allowed} allowed)")
                for k in reversed(keys):
                    if over <= 0: break
                    cut = min(cart[k], over); cart[k] -= cut; over -= cut
                    if cart[k] <= 0: cart.pop(k, None)
    if user and user['role'] == 'customer':
        with db() as con:
            con.execute('DELETE FROM user_cart_items WHERE user_id=?', (user['id'],))
            for key, qty in cart.items():
                parsed = parse_cart_key(key)
                if not parsed: continue
                pid, mode, size = parsed
                con.execute('INSERT INTO user_cart_items(user_id,cart_key,product_id,mode,size,qty,updated_at) VALUES(?,?,?,?,?,?,?)', (user['id'], key, pid, mode, size, qty, now()))
    session['cart'] = cart
    session['checkout_key'] = secrets.token_urlsafe(24)
    flash('Cart updated and saved to your account.', 'success')
    if limited_products:
        flash('Quantity reduced to the purchase limit for: ' + ', '.join(limited_products) + '.', 'warning')
    return redirect(url_for('cart'))

def create_order(form, method):
    user = current_user()
    if not user or user['role'] == 'admin':
        flash('Please log in with a customer account before placing an order.', 'warning')
        return None
    items, subtotal, count = cart_data()
    if not items:
        flash('Your cart is empty.', 'warning'); return None
    name = form.get('customer_name', '').strip()
    email = form.get('customer_email', '').strip().lower()
    phone = form.get('customer_phone', '').strip()
    address = form.get('address', '').strip()
    is_null_customer = bool(user and str(user['email']).lower() == DEMO_CUSTOMER_EMAIL)
    if is_null_customer:
        # The null/null login identity fields are fixed server-side, not just readonly in HTML.
        name = user['name']
        email = user['email']
        phone = user['phone'] or '0000000000'
        address = user['address'] or DEMO_CUSTOMER_ADDRESS
    if not name or not email or not phone:
        flash('Please fill in your name, email and phone number.', 'danger'); return None
    if len(phone) < 8:
        flash('Please check your phone number.', 'danger'); return None
    if method in ('COD', 'Online') and (len(address) < 8 or address.lower().startswith('please edit address')):
        flash('A full delivery address is required for this delivery method. Please enter your house, street, area and PIN code.', 'danger'); return None
    if method in ('COD', 'Online'):
        pin_ok, pin_msg = check_address_pin(address)
        if not pin_ok:
            flash(pin_msg, 'danger'); return None
    created_now = False
    wanted = {}
    names = {}
    if user['role'] == 'customer':
        for entry in items:
            wanted[entry['id']] = wanted.get(entry['id'], 0) + int(entry['qty'])
            names[entry['id']] = entry['name']
        with db() as con:
            for pid, qty_wanted in wanted.items():
                row = con.execute('SELECT max_per_user,stock FROM products WHERE id=?', (pid,)).fetchone()
                if row and row['stock'] is not None and int(row['stock']) < qty_wanted:
                    flash(f"{names[pid]} is sold out." if int(row['stock']) <= 0 else f"Only {int(row['stock'])} of {names[pid]} left. Please reduce the quantity in your cart.", 'warning')
                    return None
                limit = int(row['max_per_user'] or 0) if row else 0
                if limit and qty_wanted + user_purchased_qty(con, user['id'], pid) > limit:
                    flash(f"{names[pid]} is limited to {limit} per customer. Please reduce the quantity in your cart.", 'warning')
                    return None
    coupon_code = (form.get('coupon_code') or session.get('coupon_code') or '').strip().upper()[:32]
    discount = 0.0
    key = session.get('checkout_key') or secrets.token_urlsafe(24)
    try:
        with db() as con:
            if coupon_code:
                coupon = con.execute('SELECT * FROM coupons WHERE code=? AND active=1', (coupon_code,)).fetchone()
                if not coupon: flash('Coupon code is invalid or inactive.', 'danger'); return None
                if subtotal < float(coupon['min_order']): flash(f"Coupon requires a minimum order of ₹{coupon['min_order']:.0f}.", 'warning'); return None
                if int(coupon['max_uses']) > 0 and int(coupon['used_count']) >= int(coupon['max_uses']): flash('Coupon usage limit reached.', 'warning'); return None
                discount = min(float(coupon['discount_amount']), subtotal)
                con.execute('UPDATE coupons SET used_count=used_count+1 WHERE id=?', (coupon['id'],))
            total = max(0, round(subtotal - discount, 2))
            for pid, qty_wanted in wanted.items():
                taken = con.execute('UPDATE products SET stock=stock-?, updated_at=? WHERE id=? AND stock IS NOT NULL AND stock>=?', (qty_wanted, now(), pid, qty_wanted)).rowcount
                if not taken and con.execute('SELECT stock FROM products WHERE id=?', (pid,)).fetchone()['stock'] is not None:
                    raise StockError(f"{names[pid]} just sold out or has fewer than {qty_wanted} left.")
            packed_items = json.dumps(items, ensure_ascii=False)
            token = secrets.token_urlsafe(24)
            con.execute('''INSERT INTO orders(token,idempotency_key,user_id,customer_name,customer_email,customer_phone,address,items_json,total,subtotal,coupon_code,discount,delivery_charge_status,payment_method,status,payment_status,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (token, key, user['id'] if user else None, name, email, phone, address if method in ('COD', 'Online') else '', packed_items, total, subtotal, coupon_code, discount,
                         'pending' if method in ('COD', 'Online') else '', method,
                         'Awaiting delivery charge quote' if method in ('COD', 'Online') else 'Cart QR Ready',
                         'Awaiting delivery charge quote' if method in ('COD', 'Online') else 'Not paid', now(), now()))
            order_id = con.execute('SELECT id FROM orders WHERE idempotency_key=?', (key,)).fetchone()['id']
            order = con.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
            created_now = True
            if not con.execute('SELECT 1 FROM order_status_history WHERE order_id=? LIMIT 1', (order_id,)).fetchone():
                add_status_history(con, order_id, '', order['status'], '', order['payment_status'], user['id'], 'Order created')
            if user and method in ('COD', 'Online') and address and not is_null_customer:
                con.execute('UPDATE users SET address=?, phone=? WHERE id=?', (address, phone, user['id']))
    except StockError as exc:
        flash(str(exc), 'warning'); return None
    except sqlite3.IntegrityError:
        with db() as con:
            order = con.execute('SELECT * FROM orders WHERE idempotency_key=?', (key,)).fetchone()
        if not order:
            flash('This checkout could not be saved. Please try again.', 'danger'); return None
        order_id = order['id']
    recent_tokens = session.get('recent_order_tokens', [])
    if order['token'] not in recent_tokens:
        recent_tokens.append(order['token'])
    session['recent_order_tokens'] = recent_tokens[-15:]
    if method in ('COD', 'Online') and address:
        session['saved_address'] = address
        session['saved_phone'] = phone
    if user and user['role'] == 'customer':
        with db() as con:
            con.execute('DELETE FROM user_cart_items WHERE user_id=?', (user['id'],))
    session['cart'] = {}; session.pop('checkout_key', None); session.pop('coupon_code', None)
    if created_now:
        send_owner_whatsapp(order_alert_text(order, items, method))
    if method in ('COD', 'Online'):
        label = 'cash-on-delivery' if method == 'COD' else 'online-payment'
        notify_admins(f'New {label} order', f"Order #{order_id}: {name}, ₹{order['total']:.2f}. Review the delivery location and send a delivery-charge quote.")
        payment_label = 'COD' if method == 'COD' else 'online-payment'
        next_step = 'preparation' if method == 'COD' else 'UPI payment'
        notify_one(user['id'], 'Delivery charge review', f'Your {payment_label} order #{order_id} is saved. The store will review your location and send the delivery charge for approval before {next_step}.')
    return order



@app.route('/coupon/apply', methods=['POST'])
def apply_coupon():
    user = current_user()
    if user and user['role'] == 'admin':
        flash('Admin accounts do not use customer coupons.', 'warning')
        return redirect(url_for('admin_dashboard'))
    code = request.form.get('coupon_code', '').strip().upper()[:32]
    items, subtotal, _ = cart_data()
    if not items:
        flash('Add products before applying a coupon.', 'warning')
    else:
        with db() as con:
            coupon = con.execute('SELECT * FROM coupons WHERE code=? AND active=1', (code,)).fetchone()
        if not coupon:
            flash('Coupon code is invalid or inactive.', 'danger')
        elif subtotal < float(coupon['min_order']):
            flash(f"This coupon needs a minimum cart value of ₹{coupon['min_order']:.0f}.", 'warning')
        elif int(coupon['max_uses']) > 0 and int(coupon['used_count']) >= int(coupon['max_uses']):
            flash('This coupon has reached its usage limit.', 'warning')
        else:
            session['coupon_code'] = code
            flash(f"Coupon {code} applied — save ₹{min(float(coupon['discount_amount']), subtotal):.0f}.", 'success')
    return redirect(url_for('cart'))

@app.route('/coupon/remove', methods=['POST'])
def remove_coupon():
    user = current_user()
    if user and user['role'] == 'admin':
        session.pop('coupon_code', None)
        flash('Admin accounts do not use customer coupons.', 'warning')
        return redirect(url_for('admin_dashboard'))
    session.pop('coupon_code', None)
    flash('Coupon removed.', 'info')
    return redirect(url_for('cart'))

@app.route('/admin/coupons', methods=['GET', 'POST'])
@admin_required
def admin_coupons():
    if request.method == 'POST':
        code = ''.join(ch for ch in request.form.get('code', '').strip().upper() if ch.isalnum() or ch in '_-')[:32]
        try:
            amount = float(request.form.get('discount_amount', '0'))
            minimum = float(request.form.get('min_order', '0'))
            max_uses = max(0, int(request.form.get('max_uses', '0')))
            if not code or amount <= 0 or minimum < 0: raise ValueError
        except ValueError:
            flash('Enter a valid coupon, discount and minimum order.', 'danger')
            return redirect(url_for('admin_coupons'))
        try:
            with db() as con:
                con.execute('INSERT INTO coupons(code,discount_amount,min_order,max_uses,active,created_at) VALUES(?,?,?,?,1,?)', (code, amount, minimum, max_uses, now()))
            flash(f'Coupon {code} created.', 'success')
        except sqlite3.IntegrityError:
            flash('That coupon code already exists.', 'warning')
        return redirect(url_for('admin_coupons'))
    with db() as con: coupons = con.execute('SELECT * FROM coupons ORDER BY id DESC').fetchall()
    return render_template('admin_coupons.html', coupons=coupons)

@app.route('/admin/coupons/<int:coupon_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_coupon(coupon_id):
    with db() as con:
        coupon = con.execute('SELECT * FROM coupons WHERE id=?', (coupon_id,)).fetchone()
    if not coupon:
        abort(404)
    if request.method == 'POST':
        code = ''.join(ch for ch in request.form.get('code', '').strip().upper() if ch.isalnum() or ch in '_-')[:32]
        try:
            amount = float(request.form.get('discount_amount', '0'))
            minimum = float(request.form.get('min_order', '0'))
            max_uses = max(0, int(request.form.get('max_uses', '0')))
            active = 1 if request.form.get('active') == '1' else 0
            if not code or amount <= 0 or minimum < 0: raise ValueError
        except ValueError:
            flash('Enter a valid coupon code, discount, minimum order and usage limit.', 'danger')
            return redirect(url_for('edit_coupon', coupon_id=coupon_id))
        try:
            with db() as con:
                con.execute('UPDATE coupons SET code=?,discount_amount=?,min_order=?,max_uses=?,active=? WHERE id=?',
                            (code, amount, minimum, max_uses, active, coupon_id))
            flash(f'Coupon {code} updated.', 'success')
            return redirect(url_for('admin_coupons'))
        except sqlite3.IntegrityError:
            flash('Another coupon already uses that code. Choose a unique code.', 'warning')
            return redirect(url_for('edit_coupon', coupon_id=coupon_id))
    return render_template('admin_coupon_edit.html', coupon=coupon)

@app.route('/admin/coupons/<int:coupon_id>/delete', methods=['POST'])
@admin_required
def delete_coupon(coupon_id):
    with db() as con:
        coupon = con.execute('SELECT code FROM coupons WHERE id=?', (coupon_id,)).fetchone()
        if not coupon:
            abort(404)
        con.execute('DELETE FROM coupons WHERE id=?', (coupon_id,))
    flash(f'Coupon {coupon["code"]} deleted. Existing order records remain unchanged.', 'success')
    return redirect(url_for('admin_coupons'))

@app.route('/admin/coupons/<int:coupon_id>/toggle', methods=['POST'])
@admin_required
def toggle_coupon(coupon_id):
    with db() as con:
        row = con.execute('SELECT active FROM coupons WHERE id=?', (coupon_id,)).fetchone()
        if not row: abort(404)
        con.execute('UPDATE coupons SET active=? WHERE id=?', (0 if row['active'] else 1, coupon_id))
    flash('Coupon status updated.', 'success')
    return redirect(url_for('admin_coupons'))

@app.route('/checkout/qr', methods=['POST'])
@login_required
def checkout_qr():
    user = current_user()
    if user and user['role'] == 'admin':
        flash('Admin accounts manage the store and cannot place customer orders.', 'warning')
        return redirect(url_for('admin_dashboard'))
    order = create_order(request.form, 'QR')
    if not order:
        return redirect(url_for('cart'))
    return redirect(url_for('order_qr', token=order['token']))


@app.route('/checkout/cod', methods=['POST'])
@login_required
def checkout_cod():
    user = current_user()
    if user and user['role'] == 'admin':
        flash('Admin accounts manage the store and cannot place customer orders.', 'warning')
        return redirect(url_for('admin_dashboard'))
    order = create_order(request.form, 'COD')
    if not order:
        return redirect(url_for('cart'))
    return redirect(url_for('order_qr', token=order['token']))


@app.route('/checkout/online', methods=['POST'])
@login_required
def checkout_online():
    user = current_user()
    if user and user['role'] == 'admin':
        flash('Admin accounts manage the store and cannot place customer orders.', 'warning')
        return redirect(url_for('admin_dashboard'))
    # This is a UPI intent QR and customer self-report flow, not gateway-verified payment.
    order = create_order(request.form, 'Online')
    if not order:
        return redirect(url_for('cart'))
    return redirect(url_for('order_qr', token=order['token']))


@app.route('/order/<token>/upi-decision', methods=['POST'])
@login_required
def upi_payment_decision(token):
    user = current_user()
    if not user or user['role'] != 'customer': abort(403)
    action = request.form.get('action', '')
    notify_admin = False
    with db() as con:
        order = con.execute('SELECT * FROM orders WHERE token=? AND user_id=?', (token, user['id'])).fetchone()
        if not order: abort(404)
        if order['payment_method'] != 'Online' or order['status'] in ('Delivered','Completed','Cancelled'):
            flash('This order no longer accepts payment decisions.', 'warning')
            return redirect(url_for('order_qr', token=token))
        if action == 'paid':
            if order['status'] != 'Awaiting UPI payment':
                flash('Your payment report is already awaiting store review.', 'info')
            else:
                fresh = con.execute('SELECT * FROM orders WHERE id=?', (order['id'],)).fetchone()
                set_order_state(con, fresh, status='UPI payment reported', payment_status='Customer reports paid — awaiting store verification', actor_id=user['id'], note='Customer reported UPI payment; not verified by system')
                notify_admin = True
                flash('Payment report sent. The store must verify the payment in its own account before registering the order.', 'success')
        elif action == 'not_paid':
            flash('No payment report was sent. Your order remains in My Account, where you can try again or cancel it.', 'info')
        elif action == 'change_decision':
            if order['status'] != 'UPI payment reported':
                flash('There is no payment report to change.', 'warning')
            else:
                fresh = con.execute('SELECT * FROM orders WHERE id=?', (order['id'],)).fetchone()
                set_order_state(con, fresh, status='Awaiting UPI payment', payment_status='Pending payment', actor_id=user['id'], note='Customer changed payment decision before store verification')
                flash('Payment decision changed. You can continue with the UPI QR or cancel this order.', 'success')
        else:
            abort(400)
        order_id = order['id']; total = float(order['total'])
    if notify_admin:
        notify_admins('Customer reports UPI payment', f'Order #{order_id}, ₹{total:.2f}. Customer says they paid. Verify the transaction in the merchant UPI/bank account before confirming this order.')
    return redirect(url_for('order_qr', token=token))


@app.route('/payment/verify', methods=['POST'])
def verify_payment():
    key_id = os.environ.get('RAZORPAY_KEY_ID', '').strip()
    key_secret = os.environ.get('RAZORPAY_KEY_SECRET', '').strip()
    if not key_id or not key_secret:
        return jsonify({'ok': False, 'message': 'Payment gateway is not configured.'}), 400
    payload = request.get_json(silent=True) or {}
    try:
        import razorpay
        client = razorpay.Client(auth=(key_id, key_secret))
        client.utility.verify_payment_signature({
            'razorpay_order_id': payload['razorpay_order_id'],
            'razorpay_payment_id': payload['razorpay_payment_id'],
            'razorpay_signature': payload['razorpay_signature'],
        })
        with db() as con:
            row = con.execute("SELECT * FROM orders WHERE id=?", (int(payload['local_order_id']),)).fetchone()
            if not row:
                return jsonify({'ok': False, 'message': 'Order not found.'}), 404
            if not row['gateway_order_id'] or row['gateway_order_id'] != payload['razorpay_order_id']:
                return jsonify({'ok': False, 'message': 'This payment does not match this order.'}), 400
            set_order_state(con, row, status='Confirmed', payment_status='Paid', actor_id=session.get('user_id'), note='Payment gateway signature verified')
        notify_admins('Online payment confirmed', f"Order #{row['id']} has a payment signature verified by the gateway.")
        return jsonify({'ok': True, 'redirect': url_for('order_qr', token=row['token'])})
    except Exception:
        return jsonify({'ok': False, 'message': 'Payment verification failed. Do not treat this order as paid.'}), 400


@app.route('/order/<token>')
def order_qr(token):
    order = get_order_by_token(token)
    if not order: abort(404)
    user = current_user()
    if order['user_id']:
        if not user:
            return redirect(url_for('login', next=request.path))
        if user['role'] != 'admin' and order['user_id'] != user['id']:
            abort(403)
    items = order_items(order)
    qr_data = None
    qr_error = ''
    admin_order_qr_data = None
    admin_order_qr_error = ''
    upi_uri = ''
    qr_payload = ''
    terminal = order['status'] in ('Delivered', 'Completed', 'Cancelled')
    if not terminal:
        # Always create a separate store-workflow QR. The payment QR for UPI is a different code.
        admin_qr_path = url_for('admin_qr_order', token=order['token'])
        admin_qr_payload = external_store_url(admin_qr_path)
        admin_order_qr_data = create_qr_data_uri(admin_qr_payload)
        if not admin_order_qr_data:
            admin_order_qr_error = ('The QR image library is not available. Install the dependencies from requirements.txt and restart the website.'
                                    if qrcode is None else 'The order QR image could not be generated. Refresh the page and try again.')
        if order['payment_method'] == 'Online' and order['status'] == 'Awaiting UPI payment':
            amount = f"{float(order['total']):.2f}"
            upi_uri = f"upi://pay?pa=7909033393@fam&pn=Prassddhi%20Acharya&am={amount}&cu=INR"
            qr_payload = upi_uri
            qr_data = create_qr_data_uri(qr_payload)
            if not qr_data:
                qr_error = ('The QR image library is not available. Install the dependencies from requirements.txt and restart the website.'
                            if qrcode is None else 'The UPI payment QR could not be generated. Refresh the page and try again.')
        elif order['payment_method'] == 'Online':
            qr_payload = ''
        else:
            qr_payload = admin_qr_payload
            qr_data = admin_order_qr_data
            qr_error = admin_order_qr_error
    display_order = dict(order)
    is_owner_customer = bool(user and user['role'] == 'customer' and order['user_id'] == user['id'])
    if not is_owner_customer:
        display_order['confirmation_code'] = ''
    reviews = []
    return render_template('order_qr.html', order=display_order, items=items, qr_data=qr_data, qr_error=qr_error, admin_order_qr_data=admin_order_qr_data, admin_order_qr_error=admin_order_qr_error, upi_uri=upi_uri,
                           is_owner_customer=is_owner_customer)


@app.route('/order/<token>/cod-decision', methods=['POST'])
@app.route('/order/<token>/delivery-charge-decision', methods=['POST'])
@login_required
def cod_charge_decision(token):
    """Customer accepts or declines the delivery fee before COD prep or online payment."""
    user = current_user()
    if not user or user['role'] != 'customer':
        abort(403)
    action = request.form.get('action', '')
    notify_admin = False
    with db() as con:
        order = con.execute('SELECT * FROM orders WHERE token=? AND user_id=?', (token, user['id'])).fetchone()
        if not order:
            abort(404)
        method = order['payment_method']
        fee_status = order['delivery_charge_status'] or order['cod_charge_status']
        quote_states = ('Awaiting delivery charge approval', 'Awaiting COD charge approval')
        if method not in ('COD', 'Online') or order['status'] not in quote_states or fee_status != 'quoted':
            flash('There is no delivery-charge quote waiting for your response.', 'warning')
            return redirect(url_for('order_qr', token=token))
        extra_charge = float(order['delivery_charge'] or order['cod_extra_charge'] or 0)
        if action == 'accept':
            con.execute("UPDATE orders SET delivery_charge_status='accepted', cod_charge_status=CASE WHEN payment_method='COD' THEN 'accepted' ELSE cod_charge_status END, updated_at=? WHERE id=?", (now(), order['id']))
            fresh = con.execute('SELECT * FROM orders WHERE id=?', (order['id'],)).fetchone()
            if method == 'COD':
                next_status, next_payment = 'COD requested', 'Pay on delivery'
                note = f'Customer accepted delivery charge ₹{extra_charge:.2f}; COD order confirmed for processing'
                flash('Delivery charge accepted. Your COD order is confirmed for store processing.', 'success')
            else:
                next_status, next_payment = 'Awaiting UPI payment', 'Pending payment'
                note = f'Customer accepted delivery charge ₹{extra_charge:.2f}; updated UPI total ready for payment'
                flash('Delivery charge accepted. Your updated total is ready for UPI payment.', 'success')
            set_order_state(con, fresh, status=next_status, payment_status=next_payment, actor_id=user['id'], note=note)
            notify_admin = True
        elif action == 'decline':
            set_order_state(con, order, status='Cancelled', payment_status='Delivery charge declined', actor_id=user['id'], note='Customer declined the quoted delivery charge')
            con.execute("UPDATE orders SET delivery_charge_status='declined', cod_charge_status=CASE WHEN payment_method='COD' THEN 'declined' ELSE cod_charge_status END, updated_at=? WHERE id=?", (now(), order['id']))
            restore_stock(con, order)
            flash('The order was cancelled because the delivery charge was not accepted.', 'info')
        else:
            abort(400)
        order_id = int(order['id'])
    if notify_admin:
        suffix = ' The COD order can now enter preparation.' if method == 'COD' else ' The customer can now pay the updated online total.'
        notify_admins('Customer accepted delivery charge', f'Order #{order_id} accepted the ₹{extra_charge:.2f} delivery charge.' + suffix)
    else:
        notify_admins('Customer declined delivery charge', f'Order #{order_id} was cancelled because the customer declined the delivery charge.')
    return redirect(url_for('order_qr', token=token))


@app.route('/order/<token>/cancel', methods=['POST'])
@login_required
def customer_cancel_order(token):
    user = current_user()
    if not user or user['role'] == 'admin':
        abort(403)
    with db() as con:
        order = con.execute('SELECT * FROM orders WHERE token=? AND user_id=?', (token, user['id'])).fetchone()
        if not order:
            abort(404)
        if order['status'] in ('Delivered', 'Completed', 'Cancelled'):
            flash('This order can no longer be cancelled.', 'warning')
            return redirect(url_for('order_qr', token=token))
        if order['status'] in ('On the way', 'Awaiting delivery code'):
            flash('This order is out for delivery. Please contact the store to request help.', 'warning')
            return redirect(url_for('order_qr', token=token))
        set_order_state(con, order, status='Cancelled', actor_id=user['id'], note='Customer cancelled order')
        restore_stock(con, order)
    notify_admins('Customer cancelled order', f'Order #{order["id"]} was cancelled by the customer.')
    flash('Your order has been cancelled.', 'success')
    return redirect(url_for('order_qr', token=token))


@app.route('/admin/qr/<token>')
@admin_required
def admin_qr_order(token):
    order = get_order_by_token(token)
    if not order:
        abort(404)
    return redirect(url_for('admin_order_detail', order_id=order['id']) + '#order-workflow')


@app.route('/admin/scan')
@admin_required
def admin_scan():
    return render_template('admin_scan.html')


@app.route('/admin')
@admin_required
def admin_dashboard():
    with db() as con:
        stats = {
            'products': con.execute('SELECT COUNT(*) FROM products WHERE active=1').fetchone()[0],
            'users': con.execute("SELECT COUNT(*) FROM users WHERE role='customer' AND lower(email)<>?", (DEMO_CUSTOMER_EMAIL,)).fetchone()[0],
            'orders': con.execute('SELECT COUNT(*) FROM orders').fetchone()[0],
            'sales': con.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status IN ('Confirmed','Order confirmed','COD requested','Preparing item','Ready for dispatch','On the way','Awaiting delivery code','Preparing','Ready','Completed','Delivered')").fetchone()[0],
            'pending': con.execute("SELECT COUNT(*) FROM orders WHERE status IN ('Awaiting delivery charge quote','Awaiting delivery charge approval','Awaiting COD charge quote','Awaiting COD charge approval','COD requested','Order confirmed','Cart QR Ready','Awaiting online payment')").fetchone()[0],
        }
        stats['returns'] = con.execute("SELECT COUNT(*) FROM return_requests WHERE status IN ('Requested','Approved','Item received')").fetchone()[0]
        recent_orders = con.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 6').fetchall()
        recent_users = con.execute("SELECT * FROM users WHERE role='customer' AND lower(email)<>? ORDER BY id DESC LIMIT 5", (DEMO_CUSTOMER_EMAIL,)).fetchall()
        order_snapshot = order_change_snapshot(con)
    return render_template('admin_dashboard.html', stats=stats, recent_orders=recent_orders, recent_users=recent_users, order_snapshot=order_snapshot, backups=list_backups()[:3], wa_ready=whatsapp_alerts_ready(), auto_backup=AUTO_BACKUP)


def save_image(file_storage):
    if not file_storage or not file_storage.filename:
        return ''
    original = secure_filename(file_storage.filename)
    ext = original.rsplit('.', 1)[-1].lower() if '.' in original else ''
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError('Use a JPG, PNG, or WebP image.')
    data = file_storage.read()
    if not data:
        return ''
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Image is over 8 MB. Please choose a smaller photo.')
    # Validate file contents as well as the extension; do not trust filenames from a browser.
    try:
        from PIL import Image, UnidentifiedImageError
        Image.MAX_IMAGE_PIXELS = 40_000_000
        with Image.open(io.BytesIO(data)) as uploaded_image:
            if uploaded_image.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError('Use a JPG, PNG, or WebP image.')
            uploaded_image.verify()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('This file is not a valid JPG, PNG, or WebP image. Please choose a different photo.') from exc
    # The browser compresses/resizes photos before upload; this server check keeps uploads bounded.
    filename = secrets.token_hex(12) + '.' + ext
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, 'wb') as f:
        f.write(data)
    return filename


@app.route('/admin/products')
@admin_required
def admin_products():
    with db() as con:
        products = con.execute('SELECT * FROM products ORDER BY id DESC').fetchall()
    return render_template('admin_products.html', products=products)


@app.route('/admin/products/new', methods=['GET', 'POST'])
@admin_required
def admin_product_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = (request.form.get('custom_category', '').strip() or request.form.get('category', '').strip())[:60]
        description = request.form.get('description', '').strip()
        offer = request.form.get('offer', 'buy')
        try:
            max_per_user = max(0, min(1000, int(request.form.get('max_per_user', '0').strip() or 0)))
        except ValueError:
            max_per_user = 0
        try:
            stock_raw = request.form.get('stock', '').strip()
            stock = None if stock_raw == '' else max(0, min(1000000, int(stock_raw)))
        except ValueError:
            stock = None
        try:
            rental_raw = request.form.get('rental_price', '').strip()
            rental_price = float(rental_raw) if rental_raw else None
            if offer not in ('buy','rent','both') or (offer in ('rent','both') and (rental_price is None or rental_price <= 0)): raise ValueError
            price = float(request.form.get('price', '0'))
            if price <= 0 or price > 10000000:
                raise ValueError
        except ValueError:
            flash('Enter a valid product price.', 'danger')
            return render_template('product_form.html', product=None, categories=available_categories())
        if not name or not category:
            flash('Please enter a product name and choose or enter a category.', 'danger')
            return render_template('product_form.html', product=None, categories=available_categories())
        try:
            uploads = request.files.getlist('images') + request.files.getlist('camera_images')
            legacy_image = request.files.get('image')
            if legacy_image and legacy_image.filename:
                uploads = [legacy_image] + uploads
            image_names = save_product_images(uploads)
            image = image_names[0] if image_names else ''
        except ValueError as e:
            flash(str(e), 'danger')
            return render_template('product_form.html', product=None, categories=available_categories())
        with db() as con:
            con.execute('INSERT INTO products(name,category,description,price,rental_price,offer,image,active,created_at,updated_at,max_per_user,stock,sizes,colours) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (name, category, description, price, rental_price, offer, image, 1, now(), now(), max_per_user, stock, ', '.join(parse_sizes(request.form.get('sizes', ''))), ', '.join(parse_colours(request.form.get('colours', '')))))
            product_id = con.execute('SELECT last_insert_rowid()').fetchone()[0]
            attach_product_images(con, product_id, image_names)
        notify_users('New style added', f'{name} is now available in {category}. Visit Shreejee Fashion to explore it.')
        flash('Product added successfully. Customers have been notified.', 'success')
        return redirect(url_for('admin_products'))
    return render_template('product_form.html', product=None, categories=available_categories())


@app.route('/admin/products/<int:product_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_product_edit(product_id):
    with db() as con:
        product = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
    if not product:
        abort(404)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = (request.form.get('custom_category', '').strip() or request.form.get('category', '').strip())[:60]
        description = request.form.get('description', '').strip()
        offer = request.form.get('offer', 'buy')
        try:
            max_per_user = max(0, min(1000, int(request.form.get('max_per_user', '0').strip() or 0)))
        except ValueError:
            max_per_user = 0
        try:
            stock_raw = request.form.get('stock', '').strip()
            stock = None if stock_raw == '' else max(0, min(1000000, int(stock_raw)))
        except ValueError:
            stock = None
        try:
            rental_raw = request.form.get('rental_price', '').strip()
            rental_price = float(rental_raw) if rental_raw else None
            if offer not in ('buy','rent','both') or (offer in ('rent','both') and (rental_price is None or rental_price <= 0)): raise ValueError
            price = float(request.form.get('price', '0'))
            if price <= 0 or price > 10000000:
                raise ValueError
        except ValueError:
            flash('Enter a valid product price.', 'danger')
            return render_template('product_form.html', product=product, categories=available_categories())
        if not name or not category:
            flash('Please enter a product name and choose or enter a category.', 'danger')
            return render_template('product_form.html', product=product, categories=available_categories())
        image = product['image']
        try:
            uploads = request.files.getlist('images') + request.files.getlist('camera_images')
            legacy_image = request.files.get('image')
            if legacy_image and legacy_image.filename:
                uploads = [legacy_image] + uploads
            new_images = save_product_images(uploads)
            existing_images = product_image_list(product_id, product['image'])
            if len(existing_images) + len(new_images) > 8:
                for filename in new_images:
                    try: os.remove(os.path.join(UPLOAD_DIR, filename))
                    except OSError: pass
                flash(f'This product already has {len(existing_images)} photo(s). Choose no more than {max(0, 8-len(existing_images))} additional photo(s).', 'warning')
                return render_template('product_form.html', product=product, categories=available_categories(), images=existing_images)
            if new_images:
                image = new_images[0]
        except ValueError as e:
            flash(str(e), 'danger')
            return render_template('product_form.html', product=product, categories=available_categories(), images=product_image_list(product_id, product['image']))
        with db() as con:
            con.execute('UPDATE products SET name=?,category=?,description=?,price=?,rental_price=?,offer=?,image=?,updated_at=?,max_per_user=?,stock=?,sizes=?,colours=? WHERE id=?', (name, category, description, price, rental_price, offer, image, now(), max_per_user, stock, ', '.join(parse_sizes(request.form.get('sizes', ''))), ', '.join(parse_colours(request.form.get('colours', ''))), product_id))
            current_count = con.execute('SELECT COUNT(*) FROM product_images WHERE product_id=?', (product_id,)).fetchone()[0]
            if current_count == 0 and product['image']:
                attach_product_images(con, product_id, [product['image']], 0)
                current_count = 1
            attach_product_images(con, product_id, new_images, current_count)
        flash('Product updated.', 'success')
        return redirect(url_for('admin_products'))
    return render_template('product_form.html', product=product, categories=available_categories(), images=product_image_list(product_id, product['image']))


@app.route('/admin/products/<int:product_id>/toggle', methods=['POST'])
@admin_required
def admin_product_toggle(product_id):
    with db() as con:
        product = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
        if not product:
            abort(404)
        con.execute('UPDATE products SET active=?,updated_at=? WHERE id=?', (0 if product['active'] else 1, now(), product_id))
    flash('Product visibility updated.', 'success')
    return redirect(url_for('admin_products'))



@app.route('/admin/products/<int:product_id>/delete', methods=['POST'])
@admin_required
def admin_product_delete(product_id):
    with db() as con:
        product = con.execute('SELECT image FROM products WHERE id=?', (product_id,)).fetchone()
        if not product: abort(404)
        filenames = [r['filename'] for r in con.execute('SELECT filename FROM product_images WHERE product_id=?', (product_id,)).fetchall()]
        if product['image']: filenames.append(product['image'])
        con.execute('DELETE FROM products WHERE id=?', (product_id,))
    for filename in set(filenames):
        try: os.remove(os.path.join(UPLOAD_DIR, filename))
        except OSError: pass
    flash('Product permanently deleted.', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/users')
@admin_required
def admin_users():
    q = request.args.get('q', '').strip()
    with db() as con:
        if q:
            users = con.execute("SELECT * FROM users WHERE role='customer' AND lower(email)<>? AND (name LIKE ? OR email LIKE ? OR phone LIKE ?) ORDER BY id DESC", (DEMO_CUSTOMER_EMAIL, f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
        else:
            users = con.execute("SELECT * FROM users WHERE role='customer' AND lower(email)<>? ORDER BY id DESC", (DEMO_CUSTOMER_EMAIL,)).fetchall()
    return render_template('admin_users.html', users=users, q=q)


@app.route('/admin/users/<int:user_id>/<action>', methods=['POST'])
@admin_required
def admin_user_action(user_id, action):
    if action not in ('verify', 'unverify', 'block', 'unblock'):
        abort(404)
    notification = None
    with db() as con:
        user = con.execute("SELECT * FROM users WHERE id=? AND role='customer'", (user_id,)).fetchone()
        if not user:
            abort(404)
        if action in ('verify', 'unverify'):
            con.execute('UPDATE users SET verified=? WHERE id=?', (1 if action == 'verify' else 0, user_id))
            notification = ('Account verification update', 'Your account has been verified.' if action == 'verify' else 'Your account verification status has changed.')
        else:
            con.execute('UPDATE users SET blocked=? WHERE id=?', (1 if action == 'block' else 0, user_id))
            if action == 'block':
                notification = ('Account access restricted', 'Your account is currently blocked. Please contact the store if you believe this is an error.')
    if notification:
        notify_one(user_id, notification[0], notification[1], '/account')
    flash('Customer account updated.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/orders')
@admin_required
def admin_orders():
    status_filter = request.args.get('status', '')
    with db() as con:
        if status_filter:
            orders = con.execute('SELECT * FROM orders WHERE status=? ORDER BY id DESC', (status_filter,)).fetchall()
        else:
            orders = con.execute('SELECT * FROM orders ORDER BY id DESC').fetchall()
        order_snapshot = order_change_snapshot(con)
    return render_template('admin_orders.html', orders=orders, status_filter=status_filter, order_snapshot=order_snapshot)


@app.route('/admin/orders/<int:order_id>')
@admin_required
def admin_order_detail(order_id):
    with db() as con:
        order = con.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if not order:
        abort(404)
    with db() as con:
        history = con.execute('SELECT h.*, u.name AS actor_name FROM order_status_history h LEFT JOIN users u ON u.id=h.actor_user_id WHERE h.order_id=? ORDER BY h.id DESC LIMIT 40', (order_id,)).fetchall()
    items = order_items(order)
    for item in items:
        product_id = item.get('id') or item.get('product_id')
        if product_id:
            with db() as con:
                product = con.execute('SELECT image FROM products WHERE id=?', (product_id,)).fetchone()
            if product:
                item['admin_images'] = product_image_list(product_id, product['image'])
            else:
                item['admin_images'] = [item['image']] if item.get('image') else []
        else:
            item['admin_images'] = [item['image']] if item.get('image') else []
    return render_template('admin_order_detail.html', order=order, items=items, history=history)


@app.route('/admin/orders/<int:order_id>/status', methods=['POST'])
@admin_required
def admin_order_status(order_id):
    action = request.form.get('action', 'status')
    actor = current_user()
    notify_user = None
    notify_title = None
    notify_body = None
    with db() as con:
        order = con.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
        if not order: abort(404)
        if order['status'] in ('Completed', 'Delivered', 'Cancelled'):
            flash('This order is already complete or cancelled and cannot be changed.', 'warning')
            return redirect(url_for('admin_order_detail', order_id=order_id))

        method = order['payment_method']
        if action in ('set_cod_charge', 'set_delivery_charge'):
            allowed_methods = ('COD', 'Online')
            allowed_states = ('Awaiting delivery charge quote', 'Awaiting delivery charge approval', 'Awaiting COD charge quote', 'Awaiting COD charge approval')
            if method not in allowed_methods or order['status'] not in allowed_states:
                flash('This order is not waiting for a delivery-charge quote.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id) + '#order-workflow')
            try:
                raw_fee = request.form.get('delivery_charge', request.form.get('cod_extra_charge', '0')).strip()
                fee = round(float(raw_fee), 2)
                if fee < 0 or fee > 50000:
                    raise ValueError
            except (TypeError, ValueError):
                flash('Enter a delivery charge between ₹0 and ₹50,000.', 'danger')
                return redirect(url_for('admin_order_detail', order_id=order_id) + '#order-workflow')
            quoted_total = max(0, round(float(order['subtotal'] or 0) - float(order['discount'] or 0) + fee, 2))
            con.execute("UPDATE orders SET total=?, delivery_charge=?, delivery_charge_status='quoted', cod_extra_charge=?, cod_charge_status='quoted', updated_at=? WHERE id=?", (quoted_total, fee, fee, now(), order_id))
            refreshed = con.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
            set_order_state(con, refreshed, status='Awaiting delivery charge approval', payment_status='Awaiting customer approval of delivery charge', actor_id=actor['id'], note=f'Delivery charge ₹{fee:.2f} sent to customer for approval')
            notify_user = order['user_id']; notify_title = f'Delivery charge for order #{order_id}'
            notify_body = f'The delivery charge for your location is ₹{fee:.2f}. Your updated total is ₹{quoted_total:.2f}. Review and accept or decline the quote on your order page.'
            flash('Delivery charge sent to the customer for approval. The order will proceed only after they accept.', 'success')

        elif action == 'confirm_upi_payment':
            if method != 'Online' or order['status'] != 'UPI payment reported':
                flash('There is no customer-reported UPI payment awaiting store verification.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            if request.form.get('payment_verified') != 'yes':
                flash('Confirm that the UPI payment is visible in the store bank/UPI account.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            set_order_state(con, order, status='Order confirmed', payment_status='Paid (UPI verified manually)', actor_id=actor['id'], note='Admin manually verified UPI payment and confirmed the order')
            notify_user = order['user_id']; notify_title = f'Order #{order_id} is confirmed'
            notify_body = 'Your payment has been verified and your order is confirmed. You can track packing and delivery from My Account.'
            flash('UPI payment manually verified. Order confirmed and moved to the preparation workflow.', 'success')

        elif action == 'start_delivery_confirmation':
            if method not in ('COD', 'Online'):
                flash('Doorstep delivery codes are only used for delivery orders.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            if order['status'] != 'On the way':
                flash('Move the order to “On the way” before confirming at the doorstep.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            if method == 'Online' and order['payment_status'] != 'Paid (UPI verified manually)' and order['payment_status'] != 'Paid':
                flash('Verify the online payment before dispatching this order.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            candidate = ''
            for _ in range(100):
                value = f'{secrets.randbelow(900000) + 100000}'
                if not con.execute('SELECT 1 FROM orders WHERE confirmation_code=? LIMIT 1', (value,)).fetchone():
                    candidate = value; break
            if not candidate:
                flash('Could not create a unique delivery code. Please try again.', 'danger')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            con.execute('UPDATE orders SET confirmation_code=?, confirmation_code_used=0, confirmation_attempts=0 WHERE id=?', (candidate, order_id))
            fresh = con.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
            set_order_state(con, fresh, status='Awaiting delivery code', actor_id=actor['id'], note='Admin arrived at doorstep and initiated customer delivery code')
            notify_user = order['user_id']; notify_title = f'Delivery code for order #{order_id}'
            notify_body = f'Your delivery code is {candidate}. Tell the delivery person this six-digit code only when you are receiving your item.'
            flash('Delivery code sent to the customer’s order page and notifications. It is hidden from the admin screen.', 'success')

        elif action == 'verify_delivery_code':
            if method not in ('COD', 'Online') or order['status'] != 'Awaiting delivery code' or not order['confirmation_code'] or order['confirmation_code_used']:
                flash('No active delivery code is waiting for verification.', 'danger')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            if int(order['confirmation_attempts'] or 0) >= 10:
                flash('Too many incorrect attempts. Contact the store owner before retrying.', 'danger')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            supplied = re.sub(r'\s+', '', request.form.get('confirmation_code', ''))
            if not re.fullmatch(r'\d{6}', supplied) or not secrets.compare_digest(supplied, order['confirmation_code']):
                con.execute('UPDATE orders SET confirmation_attempts=confirmation_attempts+1, updated_at=? WHERE id=?', (now(), order_id))
                flash('Incorrect six-digit code. The order remains awaiting delivery confirmation.', 'danger')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            if method == 'COD' and request.form.get('payment_received') != 'yes':
                flash('Confirm COD payment was collected before completing the order.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            con.execute('UPDATE orders SET confirmation_code_used=1 WHERE id=?', (order_id,))
            fresh = con.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
            payment = 'Paid (COD)' if method == 'COD' else order['payment_status']
            set_order_state(con, fresh, status='Delivered', payment_status=payment, actor_id=actor['id'], note='Customer delivery code verified and item handed over')
            notify_user = order['user_id']; notify_title = f'Order #{order_id} delivered'
            notify_body = 'You have received your order. Your payment and delivery are recorded as complete.'
            flash('Delivery code verified. Order marked delivered and complete.', 'success')

        elif action == 'set_eta':
            if method not in ('COD', 'Online') or order['status'] in ('Awaiting UPI payment', 'UPI payment reported'):
                flash('Estimated arrival can be set once a delivery order is confirmed.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            eta_raw = request.form.get('eta_at', '').strip()
            eta_note = request.form.get('eta_note', '').strip()[:120]
            if eta_raw:
                try:
                    datetime.strptime(eta_raw, ETA_INPUT_FORMAT)
                except ValueError:
                    flash('Enter a valid estimated arrival date and time.', 'danger')
                    return redirect(url_for('admin_order_detail', order_id=order_id))
            con.execute('UPDATE orders SET eta_at=?, eta_note=?, updated_at=? WHERE id=?', (eta_raw, eta_note, now(), order_id))
            fresh = con.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
            eta_display = format_eta(fresh)
            if eta_display:
                notify_user = order['user_id']; notify_title = f'Order #{order_id} arrival time'
                notify_body = f'Estimated arrival: {eta_display}. We will update you if anything changes.'
                flash('Estimated arrival saved and shared with the customer.', 'success')
            else:
                flash('Estimated arrival cleared.', 'info')

        elif action == 'cancel':
            if order['status'] in ('On the way', 'Awaiting delivery code'):
                flash('This order is already out for delivery. Contact the store for help.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            set_order_state(con, order, status='Cancelled', actor_id=actor['id'], note='Admin cancelled order')
            restore_stock(con, order)
            notify_user = order['user_id']; notify_title = f'Order #{order_id} cancelled'; notify_body = 'The store cancelled this order. Contact the store for more information.'
            flash('Order cancelled.', 'success')

        elif action == 'mark_delivered':
            if method == 'COD' or method == 'Online':
                flash('Delivery orders must use the customer six-digit doorstep code.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            if request.form.get('payment_received') != 'yes' or request.form.get('customer_received') != 'yes':
                flash('Confirm both that payment was received and the customer has received the item.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            set_order_state(con, order, status='Delivered', payment_status='Paid at store', actor_id=actor['id'], note='Admin confirmed in-store payment and item handover')
            notify_user = order['user_id']; notify_title = f'Order #{order_id} delivered'; notify_body = 'You have received your order.'
            flash('Payment and handover recorded. Order marked delivered and complete.', 'success')

        elif action == 'mark_paid':
            # Legacy action is kept for older QR orders; it cannot complete an order by itself.
            if method != 'QR':
                flash('Use the payment-specific workflow for this order.', 'warning')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            if order['payment_status'] in ('Paid', 'Paid at store'):
                flash('Payment is already recorded.', 'info')
                return redirect(url_for('admin_order_detail', order_id=order_id))
            set_order_state(con, order, payment_status='Paid at store', actor_id=actor['id'], note='Admin recorded store payment received')
            notify_user = order['user_id']; notify_title = f'Payment received for order #{order_id}'; notify_body = 'The store recorded payment for your order.'
            flash('Payment recorded.', 'success')

        elif action == 'status':
            status = request.form.get('status', '')
            if method in ('COD', 'Online'):
                if method == 'Online' and order['payment_status'] not in ('Paid (UPI verified manually)', 'Paid'):
                    flash('Verify the customer-reported UPI payment before starting preparation.', 'warning')
                    return redirect(url_for('admin_order_detail', order_id=order_id))
                transitions = {
                    'COD requested': {'Preparing item', 'Cancelled'},
                    'Order confirmed': {'Preparing item', 'Cancelled'},
                    'Preparing item': {'Ready for dispatch', 'Cancelled'},
                    'Ready for dispatch': {'On the way', 'Cancelled'},
                }
                if status not in transitions.get(order['status'], set()):
                    flash('Choose the next valid packing/dispatch step. Delivery completion uses the customer code.', 'warning')
                    return redirect(url_for('admin_order_detail', order_id=order_id))
            else:
                abort(400)
            set_order_state(con, order, status=status, actor_id=actor['id'], note='Admin changed order status')
            notify_user = order['user_id']; notify_title = f'Order #{order_id} update'; notify_body = f'Your order status is now: {status}.'
            flash('Order status updated.', 'success')
        else:
            abort(400)

    if notify_user:
        notify_one(notify_user, notify_title or 'Order update', notify_body or 'Your order has been updated.')
    return redirect(url_for('admin_order_detail', order_id=order_id) + '#order-workflow')


@app.route('/admin/reports.csv')
@admin_required
def export_report():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Order ID', 'Created at', 'Customer', 'Email', 'Phone', 'Delivery address', 'Items', 'Subtotal INR', 'Coupon', 'Discount INR', 'Total INR', 'Payment method', 'Payment status', 'Order status'])
    with db() as con:
        orders = con.execute('SELECT * FROM orders ORDER BY id DESC').fetchall()
    for order in orders:
        items = order_items(order)
        desc = '; '.join(f"{x.get('name','Item')} x{x.get('qty',1)}" for x in items)
        writer.writerow([order['id'], order['created_at'], order['customer_name'], order['customer_email'], order['customer_phone'], order['address'], desc, f"{order['subtotal']:.2f}", order['coupon_code'], f"{order['discount']:.2f}", f"{order['total']:.2f}", order['payment_method'], order['payment_status'], order['status']])
    raw = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    raw.seek(0)
    return send_file(raw, mimetype='text/csv', as_attachment=True, download_name='shreejee-fashion-orders.csv')


# ---------------------------------------------------------------- wishlist / save for later
@app.route('/wishlist')
@login_required
def wishlist():
    user = current_user()
    if user['role'] != 'customer':
        return redirect(url_for('admin_dashboard'))
    with db() as con:
        rows = con.execute('SELECT p.* FROM wishlist_items w JOIN products p ON p.id=w.product_id WHERE w.user_id=? AND p.active=1 ORDER BY w.created_at DESC', (user['id'],)).fetchall()
    locked_names = locked_menu_category_names()
    rows = [row for row in rows if row['category'] not in locked_names]
    return render_template('wishlist.html', products=rows, view='all', return_to=url_for('wishlist'))


@app.route('/wishlist/toggle/<int:product_id>', methods=['POST'])
def wishlist_toggle(product_id):
    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json'
    return_to = safe_local_path(request.form.get('return_to') or request.referrer, url_for('home'))
    user = current_user()
    if not user:
        if wants_json:
            return jsonify({'ok': False, 'auth_required': True, 'message': 'Log in or create an account to save items for later.',
                            'login_url': url_for('login', next=return_to), 'register_url': url_for('register', next=return_to)}), 401
        flash('Please log in to save items for later.', 'info')
        return redirect(url_for('login', next=return_to))
    if user['role'] != 'customer':
        if wants_json: return jsonify({'ok': False, 'message': 'Only customer accounts can save items.'}), 403
        flash('Only customer accounts can save items.', 'warning'); return redirect(return_to)
    with db() as con:
        product = con.execute('SELECT id,name,category FROM products WHERE id=? AND active=1', (product_id,)).fetchone()
        if not product:
            if wants_json: return jsonify({'ok': False, 'message': 'That product is no longer available.'}), 404
            flash('That product is no longer available.', 'warning'); return redirect(return_to)
        if customer_category_locked(product['category']):
            if wants_json: return jsonify({'ok': False, 'message': 'This collection is coming soon.'}), 403
            flash('This collection is coming soon.', 'info'); return redirect(return_to)
        exists = con.execute('SELECT 1 FROM wishlist_items WHERE user_id=? AND product_id=?', (user['id'], product_id)).fetchone()
        if exists:
            con.execute('DELETE FROM wishlist_items WHERE user_id=? AND product_id=?', (user['id'], product_id)); saved = False
        else:
            con.execute('INSERT INTO wishlist_items(user_id,product_id,created_at) VALUES(?,?,?)', (user['id'], product_id, now())); saved = True
        count = con.execute('SELECT COUNT(*) FROM wishlist_items WHERE user_id=?', (user['id'],)).fetchone()[0]
    message = f"{product['name']} saved for later." if saved else f"{product['name']} removed from saved items."
    if wants_json:
        return jsonify({'ok': True, 'saved': saved, 'count': count, 'message': message})
    flash(message, 'success')
    return redirect(return_to)


@app.route('/cart/save-for-later', methods=['POST'])
def save_for_later():
    user = current_user()
    if user and user['role'] == 'admin':
        flash('Admin accounts cannot save products for later.', 'warning')
        return redirect(url_for('admin_dashboard'))
    if not user or user['role'] != 'customer':
        return redirect(url_for('login', next=url_for('cart')))
    key = request.form.get('cart_key', '')
    parsed = parse_cart_key(key)
    if not parsed:
        flash('Could not find that cart item.', 'warning'); return redirect(url_for('cart'))
    with db() as con:
        con.execute('DELETE FROM user_cart_items WHERE user_id=? AND cart_key=?', (user['id'], key))
        if con.execute('SELECT 1 FROM products WHERE id=?', (parsed[0],)).fetchone():
            con.execute('INSERT OR IGNORE INTO wishlist_items(user_id,product_id,created_at) VALUES(?,?,?)', (user['id'], parsed[0], now()))
    cart = dict(session.get('cart', {})); cart.pop(key, None); session['cart'] = cart
    session['checkout_key'] = secrets.token_urlsafe(24)
    flash('Moved to Saved items. You can add it back to your cart any time.', 'success')
    return redirect(url_for('cart'))


# ---------------------------------------------------------------- website look (layout) choice
@app.route('/appearance')
def appearance_settings():
    """Full-page storefront theme and interaction-preference settings."""
    return render_template('appearance.html')


@app.route('/ui-layout', methods=['POST'])
def set_ui_layout():
    layout = request.form.get('layout', '')
    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    user = current_user()
    if layout not in UI_LAYOUTS:
        if wants_json: return jsonify({'ok': False}), 400
        flash('That look is not available.', 'danger')
        return redirect(url_for('home'))
    user = current_user()
    if user and user['role'] == 'customer':
        with db() as con:
            con.execute('UPDATE users SET ui_layout=? WHERE id=?', (layout, user['id']))
    make_default = bool(user and user['role'] == 'admin' and request.form.get('set_as_default') == '1')
    if make_default:
        published = get_site_config(draft=False)
        published['default_ui'] = layout
        save_site_config(published, draft=False)
        draft = get_site_config(draft=True)
        draft['default_ui'] = layout
        save_site_config(draft, draft=True)
    response = jsonify({'ok': True, 'layout': layout, 'default_updated': make_default}) if wants_json else redirect(safe_local_path(request.form.get('return_to') or request.referrer, url_for('home')))
    response.set_cookie('sf_ui', layout, max_age=60 * 60 * 24 * 365, samesite='Lax', httponly=True, secure=app.config['SESSION_COOKIE_SECURE'])
    if make_default and not wants_json:
        flash(f'{UI_LAYOUTS[layout]["name"]} is now the default storefront look for new visitors.', 'success')
    return response


# ---------------------------------------------------------------- forgot / reset password
RESET_CODE_MINUTES = 30
RESET_MAX_ATTEMPTS = 5


def _phone_tail(value):
    digits = re.sub(r'\D', '', value or '')
    return digits[-10:]


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        phone_tail = _phone_tail(request.form.get('phone', ''))
        created = False
        user = None
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE lower(email)=? AND role='customer'", (email,)).fetchone() if email else None
            eligible = bool(user and not user['blocked'] and email != DEMO_CUSTOMER_EMAIL and len(phone_tail) == 10 and phone_tail == _phone_tail(user['phone']))
            if eligible:
                open_request = con.execute("SELECT id FROM password_resets WHERE user_id=? AND status IN ('Requested','Code issued')", (user['id'],)).fetchone()
                hour_ago = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
                recent = con.execute('SELECT COUNT(*) FROM password_resets WHERE user_id=? AND created_at>=?', (user['id'], hour_ago)).fetchone()[0]
                if not open_request and recent < 3:
                    con.execute("INSERT INTO password_resets(user_id,status,created_at) VALUES(?,?,?)", (user['id'], 'Requested', now()))
                    created = True
        if created:
            notify_admins('Password reset requested', f"{user['name']} ({user['phone']}) asked for a password reset. Open Password resets in the admin menu to send them a one-time code.")
        # Same message every time, so nobody can use this page to find out who has an account.
        flash('If those details match an account, the store has been told. The owner will send you a one-time code (usually on WhatsApp or by phone call). Then enter it below.', 'success')
        return redirect(url_for('reset_password'))
    return render_template('forgot_password.html')


@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        code = re.sub(r'\D', '', request.form.get('code', ''))
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        generic = 'That code is not valid or has expired. Please ask the store for a new one.'
        if len(password) < 8:
            flash('Choose a new password of at least 8 characters.', 'danger')
            return render_template('reset_password.html', email=email)
        if password != confirm:
            flash('The two passwords do not match.', 'danger')
            return render_template('reset_password.html', email=email)
        problem = None
        changed_user_id = None
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE lower(email)=? AND role='customer'", (email,)).fetchone() if email else None
            row = con.execute("SELECT * FROM password_resets WHERE user_id=? AND status='Code issued' ORDER BY id DESC LIMIT 1", (user['id'],)).fetchone() if user else None
            if not user or not row or user['blocked'] or len(code) != 6:
                problem = generic
            elif row['expires_at'] < now():
                con.execute("UPDATE password_resets SET status='Expired' WHERE id=?", (row['id'],))
                problem = generic
            elif row['attempts'] >= RESET_MAX_ATTEMPTS:
                con.execute("UPDATE password_resets SET status='Locked' WHERE id=?", (row['id'],))
                problem = 'Too many wrong codes. Please ask the store for a new code.'
            elif not check_password_hash(row['code_hash'], code):
                con.execute('UPDATE password_resets SET attempts=attempts+1 WHERE id=?', (row['id'],))
                problem = generic
            else:
                con.execute('UPDATE users SET password_hash=? WHERE id=?', (generate_password_hash(password), user['id']))
                con.execute("UPDATE password_resets SET status='Used', used_at=? WHERE id=?", (now(), row['id']))
                con.execute("UPDATE password_resets SET status='Closed' WHERE user_id=? AND status IN ('Requested','Code issued')", (user['id'],))
                changed_user_id = user['id']
        if problem:
            flash(problem, 'danger')
            return render_template('reset_password.html', email=email)
        notify_one(changed_user_id, 'Password changed', 'Your password was changed using a store-issued code. If this was not you, contact the store straight away.')
        flash('Your password has been changed. Please log in with the new password.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', email='')


@app.route('/admin/password-resets')
@admin_required
def admin_password_resets():
    issued = session.pop('issued_reset', None)
    with db() as con:
        rows = con.execute("SELECT r.*, u.name AS user_name, u.email AS user_email, u.phone AS user_phone FROM password_resets r JOIN users u ON u.id=r.user_id ORDER BY r.id DESC LIMIT 60").fetchall()
    wa = ''
    if issued:
        wa = wa_link(f"Hi {issued['name']}, your Shreejee Fashion password reset code is {issued['code']}. It works for {RESET_CODE_MINUTES} minutes. Open {url_for('reset_password', _external=True)} and enter your email, this code and a new password. Please do not share this code with anyone.", phone=issued['phone'])
    return render_template('admin_password_resets.html', rows=rows, issued=issued, wa=wa, minutes=RESET_CODE_MINUTES, now_text=now())


@app.route('/admin/password-resets/<int:rid>/issue', methods=['POST'])
@admin_required
def admin_issue_reset_code(rid):
    with db() as con:
        row = con.execute('SELECT r.*, u.name AS user_name, u.phone AS user_phone FROM password_resets r JOIN users u ON u.id=r.user_id WHERE r.id=?', (rid,)).fetchone()
        if not row or row['status'] not in ('Requested', 'Code issued'):
            flash('That request is no longer open.', 'warning')
            return redirect(url_for('admin_password_resets'))
        code = f'{secrets.randbelow(1000000):06d}'
        con.execute("UPDATE password_resets SET status='Code issued', code_hash=?, attempts=0, issued_at=?, expires_at=? WHERE id=?",
                    (generate_password_hash(code), now(), (datetime.now() + timedelta(minutes=RESET_CODE_MINUTES)).strftime('%Y-%m-%d %H:%M:%S'), rid))
    session['issued_reset'] = {'id': rid, 'code': code, 'name': row['user_name'], 'phone': row['user_phone']}
    return redirect(url_for('admin_password_resets'))


@app.route('/admin/password-resets/<int:rid>/dismiss', methods=['POST'])
@admin_required
def admin_dismiss_reset(rid):
    with db() as con:
        con.execute("UPDATE password_resets SET status='Closed' WHERE id=? AND status IN ('Requested','Code issued')", (rid,))
    flash('Request closed.', 'success')
    return redirect(url_for('admin_password_resets'))


# ---------------------------------------------------------------- returns & exchanges
@app.route('/order/<token>/return', methods=['GET', 'POST'])
@login_required
def request_return(token):
    user = current_user()
    if user['role'] != 'customer':
        abort(403)
    created = None
    with db() as con:
        order = con.execute('SELECT * FROM orders WHERE token=? AND user_id=?', (token, user['id'])).fetchone()
        if not order:
            abort(404)
        lines, closed_reason = order_return_lines(con, order)
        if request.method == 'POST':
            def back(msg):
                flash(msg, 'warning'); return redirect(url_for('request_return', token=token))
            by_idx = {l['idx']: l for l in lines}
            try:
                line = by_idx[int(request.form.get('item', ''))]
            except (KeyError, ValueError):
                return back('Choose the item you want to return or exchange.')
            kind = 'exchange' if request.form.get('kind') == 'exchange' else 'return'
            reason = request.form.get('reason', '')
            if reason not in RETURN_REASONS:
                return back('Please select a reason.')
            try: qty = max(1, min(line['remaining'], int(request.form.get('qty', '1'))))
            except ValueError: qty = 1
            details = request.form.get('details', '').strip()[:600]
            if reason == 'Other' and len(details) < 5:
                return back('Please describe the problem in a few words.')
            exchange_size = ''
            if kind == 'exchange' and line['sizes']:
                exchange_size = match_size(line['sizes'], request.form.get('exchange_size', ''))
                if not exchange_size:
                    return back('Choose the size you want instead.')
            amount = round(line['unit_paid'] * qty, 2) if kind == 'return' else 0
            con.execute("INSERT INTO return_requests(order_id,user_id,product_id,product_name,size,line_key,qty,kind,reason,details,exchange_size,status,refund_status,refund_amount,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,'Requested','Not applicable',?,?,?)",
                        (order['id'], user['id'], line['id'], line['name'], line['size'], line['key'], qty, kind, reason, details, exchange_size, amount, now(), now()))
            created = (order['id'], line['name'], kind, reason)
        history = con.execute('SELECT * FROM return_requests WHERE order_id=? ORDER BY id DESC', (order['id'],)).fetchall()
    if created:
        oid, pname, kind, reason = created
        notify_admins(f"New {kind} request", f"Order #{oid}: {pname} - {reason}. Review it in Returns & exchanges.")
        notify_one(user['id'], f"{kind.title()} request received", f"Your {kind} request for {pname} (order #{oid}) was sent to the store. We will update you here once it is reviewed.", url_for('request_return', token=token))
        flash(f"Your {kind} request was sent. The store will review it and update you here.", 'success')
        return redirect(url_for('request_return', token=token))
    return render_template('return_request.html', order=order, lines=lines, closed_reason=closed_reason, history=history, reasons=RETURN_REASONS, window=RETURN_WINDOW_DAYS)


@app.route('/admin/returns')
@admin_required
def admin_returns():
    view = request.args.get('status', 'open')
    where = {'open': "WHERE r.status IN ('Requested','Approved','Item received')", 'closed': "WHERE r.status IN ('Completed','Rejected')"}.get(view, '')
    with db() as con:
        rows = con.execute("SELECT r.*, o.customer_name, o.customer_phone, o.token AS order_token, o.total AS order_total, o.payment_method "
                           "FROM return_requests r JOIN orders o ON o.id=r.order_id " + where + " ORDER BY r.id DESC LIMIT 200").fetchall()
    return render_template('admin_returns.html', requests=rows, view=view)


@app.route('/admin/returns/<int:rid>/update', methods=['POST'])
@admin_required
def admin_return_update(rid):
    action = request.form.get('action', '')
    note = request.form.get('admin_note', '').strip()[:400]
    def money(default):
        try: return max(0.0, round(float(request.form.get('refund_amount', '')), 2))
        except (TypeError, ValueError): return default
    with db() as con:
        r = con.execute('SELECT r.*, o.token AS order_token FROM return_requests r JOIN orders o ON o.id=r.order_id WHERE r.id=?', (rid,)).fetchone()
        if not r:
            abort(404)
        status, refund_status, amount, refund_note = r['status'], r['refund_status'], float(r['refund_amount']), r['refund_note']
        is_return = r['kind'] == 'return'
        title = body = ''
        if action == 'approve' and status == 'Requested':
            status = 'Approved'
            if is_return:
                refund_status = 'Refund pending'; amount = money(amount)
            title = f"{r['kind'].title()} approved"
            body = f"Your {r['kind']} request for {r['product_name']} was approved. Please bring the item to the store."
        elif action == 'reject' and status in ('Requested', 'Approved'):
            if not note:
                flash('Add a short reason so the customer knows why it was declined.', 'warning')
                return redirect(url_for('admin_returns'))
            status, refund_status, amount = 'Rejected', 'Not applicable', 0.0
            title = f"{r['kind'].title()} request declined"
            body = f"Your {r['kind']} request for {r['product_name']} was declined. Reason: {note}"
        elif action == 'received' and status == 'Approved':
            status = 'Item received'
            if request.form.get('restock') == 'yes' and r['product_id']:
                con.execute('UPDATE products SET stock=stock+?, updated_at=? WHERE id=? AND stock IS NOT NULL', (r['qty'], now(), r['product_id']))
            title = 'Item received at the store'
            body = f"We received {r['product_name']}. " + ('Your refund is being processed.' if is_return else 'Your exchange is being prepared.')
        elif action == 'refund_done' and is_return and status == 'Item received':
            status, refund_status, amount = 'Completed', 'Refunded', money(amount)
            refund_note = request.form.get('refund_note', '').strip()[:120]
            title = 'Refund completed'
            body = f"Your refund of ₹{amount:.2f} for {r['product_name']} has been issued" + (f" ({refund_note})." if refund_note else '.')
        elif action == 'exchange_done' and not is_return and status == 'Item received':
            status = 'Completed'
            title = 'Exchange completed'
            body = f"Your exchange for {r['product_name']}" + (f" (size {r['exchange_size']})" if r['exchange_size'] else '') + ' is ready.'
        else:
            flash('That action is not available for this request right now.', 'warning')
            return redirect(url_for('admin_returns'))
        con.execute('UPDATE return_requests SET status=?,refund_status=?,refund_amount=?,refund_note=?,admin_note=?,updated_at=? WHERE id=?',
                    (status, refund_status, amount, refund_note, note or r['admin_note'], now(), rid))
    notify_one(r['user_id'], title, body + (f" Note from store: {note}" if note and action != 'reject' else ''), url_for('request_return', token=r['order_token']))
    flash(f"Request #{rid}: {title.lower()}.", 'success')
    return redirect(url_for('admin_returns', status=request.form.get('view', 'open')))


@app.route('/admin/announcement', methods=['POST'])
@admin_required
def admin_announcement():
    message = request.form.get('announcement', '').strip()[:220]
    with db() as con:
        con.execute("INSERT INTO site_settings(key,value) VALUES('announcement',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (message,))
    config = get_site_config(); config['announcement'] = message; save_site_config(config, draft=False)
    draft_config = get_site_config(draft=True); draft_config['announcement'] = message; save_site_config(draft_config, draft=True)
    if message:
        notify_users('Store announcement', message)
        flash('Announcement published and customer notifications sent.', 'success')
    else:
        flash('Announcement removed - the green bar is now hidden for customers.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/website-editor', methods=['GET', 'POST'])
@admin_required
def admin_website_editor():
    if request.method == 'POST':
        action = request.form.get('action', 'save_draft')
        if action == 'restore_defaults':
            config = dict(SITE_EDITOR_DEFAULTS)
            save_site_config(config, draft=True); save_site_config(config, draft=False)
            with db() as con:
                con.execute("INSERT INTO site_settings(key,value) VALUES('announcement',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (config['announcement'],))
            flash('Website editor restored to default settings.', 'success')
            return redirect(url_for('admin_website_editor'))
        config = clean_settings(request.form, get_site_config(draft=True))
        for field, key in [('logo_image', 'logo_filename'), ('hero_image_upload', 'hero_image')]:
            upload = request.files.get(field)
            if upload and upload.filename:
                try:
                    saved = save_image(upload)
                    if saved: config[key] = saved
                except ValueError as exc:
                    flash(str(exc), 'danger')
                    return render_template('admin_website_editor.html', config=config, ui_layouts=UI_LAYOUTS)
        save_site_config(config, draft=True)
        if action == 'publish':
            save_site_config(config, draft=False)
            with db() as con:
                con.execute("INSERT INTO site_settings(key,value) VALUES('announcement',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (config.get('announcement',''),))
            notify_users('Store website updated', 'Shreejee Fashion has published website changes and store information updates.')
            flash('Website changes published. Open customer pages will synchronize automatically.', 'success')
            response = redirect(url_for('admin_website_editor'))
            default_ui = config.get('default_ui', 'classic')
            if default_ui in UI_LAYOUTS:
                response.set_cookie('sf_ui', default_ui, max_age=60 * 60 * 24 * 365, samesite='Lax', httponly=True, secure=app.config['SESSION_COOKIE_SECURE'])
            return response
        else:
            flash('Draft saved. Customers will not see it until you publish.', 'success')
        return redirect(url_for('admin_website_editor'))
    return render_template('admin_website_editor.html', config=get_site_config(draft=True), ui_layouts=UI_LAYOUTS)


def order_change_snapshot(con):
    row = con.execute("SELECT (SELECT COUNT(*) FROM orders) AS total, (SELECT COALESCE(MAX(id),0) FROM orders) AS newest, (SELECT COALESCE(MAX(updated_at),'') FROM orders) AS last_update, (SELECT COALESCE(MAX(id),0) FROM order_status_history) AS history_id").fetchone()
    return f"{row['total']}:{row['newest']}:{row['last_update']}:{row['history_id']}"


@app.route('/api/order-updates')
@login_required
def api_order_updates():
    user = current_user()
    with db() as con:
        if user['role'] == 'admin':
            rows = con.execute('SELECT id,token,status,payment_status,updated_at,created_at FROM orders ORDER BY id DESC LIMIT 100').fetchall()
        else:
            rows = con.execute('SELECT id,token,status,payment_status,updated_at,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 30', (user['id'],)).fetchall()
        snapshot = order_change_snapshot(con) if user['role'] == 'admin' else ''
    return jsonify({'ok': True, 'orders': [dict(r) for r in rows], 'snapshot': snapshot})


@app.route('/notifications')
@login_required
def notifications():
    user = current_user()
    with db() as con:
        rows = con.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC', (user['id'],)).fetchall()
        con.execute('UPDATE notifications SET is_read=1 WHERE user_id=?', (user['id'],))
    rows = resolve_notification_actions(rows, user)
    return render_template('notifications.html', notifications=rows)


@app.route('/notifications/clear', methods=['POST'])
@login_required
def clear_notifications():
    user = current_user()
    with db() as con:
        cleared = con.execute('DELETE FROM notifications WHERE user_id=?', (user['id'],)).rowcount
    flash('All notifications cleared.' if cleared else 'There were no notifications to clear.', 'success' if cleared else 'info')
    return redirect(url_for('notifications'))


@app.route('/notifications/<int:notification_id>/delete', methods=['POST'])
@login_required
def delete_notification(notification_id):
    user = current_user()
    with db() as con:
        con.execute('DELETE FROM notifications WHERE id=? AND user_id=?', (notification_id, user['id']))
    return redirect(url_for('notifications'))


@app.route('/health', methods=['GET'])
def health_check():
    """Simple same-origin diagnostic proving the Flask storefront is responding."""
    return jsonify({'ok': True, 'service': 'Shreejee Fashion', 'version': '1.0.10'}), 200


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/privacy')
def privacy():
    return render_template('policy.html', page='privacy')


@app.route('/terms')
def terms():
    return render_template('policy.html', page='terms')


@app.route('/cookies')
def cookies():
    return render_template('policy.html', page='cookies')


@app.errorhandler(413)
def too_large(_error):
    flash('The upload was too large. Choose an image under 8 MB.', 'danger')
    return redirect(request.referrer or url_for('admin_product_new'))


@app.errorhandler(400)
def bad_request(error):
    return render_template('error.html', code=400, message=getattr(error, 'description', 'Please reload the page and try again.')), 400


@app.errorhandler(403)
def forbidden(_error):
    return render_template('error.html', code=403, message='You do not have permission to view this page.'), 403


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
        return jsonify({'ok': False, 'error': 'not_found', 'message': 'This app endpoint was not found.', 'path': request.path}), 404
    return render_template('error.html', code=404, message='We could not find that page. Check the address or return to the store homepage.'), 404


@app.errorhandler(405)
def method_not_allowed(error):
    """Turn internal method mismatches into a useful diagnostic instead of a blank/error response."""
    allowed = sorted(getattr(error, 'valid_methods', None) or [])
    message = f"The {request.method} method is not supported for {request.path}."
    if allowed:
        message += ' Supported method(s): ' + ', '.join(allowed) + '.'
    message += ' Open the store homepage and use its on-page controls; do not open a submit-only URL directly.'
    app.logger.warning('HTTP 405: method=%s path=%s allowed=%s', request.method, request.path, ','.join(allowed))
    if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
        response = jsonify({'ok': False, 'error': 'method_not_allowed', 'message': message, 'path': request.path, 'method': request.method, 'allowed_methods': allowed})
        response.status_code = 405
        if allowed:
            response.headers['Allow'] = ', '.join(allowed)
        return response
    return render_template('error.html', code=405, message=message), 405


def _cloudflared_path():
    """Find cloudflared or download the official Windows binary on first run."""
    import shutil, platform, urllib.request
    found = shutil.which('cloudflared') or shutil.which('cloudflared.exe')
    if found:
        return found
    if platform.system().lower() != 'windows':
        return None
    target = os.path.join(APP_DIR, 'cloudflared.exe')
    if os.path.isfile(target) and os.path.getsize(target) > 1_000_000:
        return target
    url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
    print('\n[Cloudflare] cloudflared was not found. Downloading the official Cloudflare Windows tool...')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'ShreejeeFashion/1.0'})
        with urllib.request.urlopen(req, timeout=45) as response, open(target + '.download', 'wb') as f:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(target + '.download', target)
        print('[Cloudflare] Download complete.')
        return target
    except Exception as exc:
        try:
            if os.path.exists(target + '.download'):
                os.remove(target + '.download')
        except OSError:
            pass
        print('[Cloudflare] Automatic download failed:', exc)
        print('Install cloudflared from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/')
        return None


def _start_cloudflare(port):
    """Start a temporary Quick Tunnel and display its public URL when available."""
    import subprocess, threading, re
    exe = _cloudflared_path()
    if not exe:
        return None
    try:
        proc = subprocess.Popen(
            [exe, 'tunnel', '--url', f'http://127.0.0.1:{port}', '--no-autoupdate'],
            cwd=APP_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        )
        _tunnel_opened = []
        def relay_logs():
            for line in proc.stdout:
                match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                if match:
                    print('\n' + '=' * 62)
                    print(' YOUR SHREEJEE FASHION PUBLIC URL:')
                    print(' ' + match.group(0))
                    print(' Open this URL on your phone or another computer.')
                    print('=' * 62 + '\n')
                    publish_tunnel_url(match.group(0))
                    if not _tunnel_opened:
                        _tunnel_opened.append(True)
                        # Give the new tunnel a few seconds to become reachable, then open it.
                        import webbrowser
                        threading.Timer(6.0, lambda url=match.group(0): webbrowser.open(url)).start()
                elif any(word in line.lower() for word in ('error', 'failed', 'unable')):
                    print('[Cloudflare]', line.rstrip())
        threading.Thread(target=relay_logs, daemon=True).start()
        time.sleep(1)
        if proc.poll() is not None:
            print('[Cloudflare] Tunnel exited. Check the messages above.')
            return None
        return proc
    except Exception as exc:
        print('[Cloudflare] Could not start tunnel:', exc)
        return None


# ================================================================ v1.0.12 features
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', '').strip()
WHATSAPP_PHONE_ID = os.environ.get('WHATSAPP_PHONE_ID', '').strip()
WHATSAPP_TEMPLATE = os.environ.get('WHATSAPP_TEMPLATE', '').strip()
WHATSAPP_TEMPLATE_LANG = os.environ.get('WHATSAPP_TEMPLATE_LANG', 'en').strip() or 'en'
CALLMEBOT_APIKEY = os.environ.get('CALLMEBOT_APIKEY', '').strip()
OWNER_ALERT_PHONE = os.environ.get('OWNER_WHATSAPP', '').strip() or OWNER_PHONE
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
FAST2SMS_API_KEY = os.environ.get('FAST2SMS_API_KEY', '').strip()
OTP_DEV_MODE = os.environ.get('SF_OTP_DEV', '0') == '1'
AUTO_BACKUP = os.environ.get('SF_AUTO_BACKUP', '1') != '0'
BACKUP_DIR = os.path.join(APP_DIR, 'backups')
BACKUP_KEEP = 14
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW_MIN = 15


def phone_tail(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits[-10:] if len(digits) >= 10 else ''


def client_ip():
    return (request.headers.get('CF-Connecting-IP') or request.remote_addr or '')[:64]


def _http_get(url, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': 'ShreejeeFashion/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', 'replace')


def _http_post_json(url, payload, headers=None, timeout=12):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json', **(headers or {})}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', 'replace')


# ---------------------------------------------------------------- WhatsApp order alerts to the owner
def whatsapp_alerts_ready():
    return bool((WHATSAPP_TOKEN and WHATSAPP_PHONE_ID) or CALLMEBOT_APIKEY)


def _send_whatsapp_now(text):
    digits = re.sub(r'\D', '', OWNER_ALERT_PHONE)
    if len(digits) == 10: digits = '91' + digits
    if WHATSAPP_TOKEN and WHATSAPP_PHONE_ID:
        if WHATSAPP_TEMPLATE:
            payload = {'messaging_product': 'whatsapp', 'to': digits, 'type': 'template', 'template': {'name': WHATSAPP_TEMPLATE, 'language': {'code': WHATSAPP_TEMPLATE_LANG}, 'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': re.sub(r'\s*\n+\s*', ' | ', text)[:900]}]}]}}
        else:
            payload = {'messaging_product': 'whatsapp', 'to': digits, 'type': 'text', 'text': {'body': text[:3500]}}
        _http_post_json(f'https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_ID}/messages', payload, {'Authorization': 'Bearer ' + WHATSAPP_TOKEN})
    elif CALLMEBOT_APIKEY:
        _http_get('https://api.callmebot.com/whatsapp.php?' + urllib.parse.urlencode({'phone': '+' + digits, 'text': text[:1500], 'apikey': CALLMEBOT_APIKEY}))


def send_owner_whatsapp(text):
    """Fire-and-forget: never slows down or breaks checkout."""
    if not whatsapp_alerts_ready():
        return
    def run():
        try:
            _send_whatsapp_now(text)
        except Exception as exc:
            print('[WhatsApp alert failed]', exc)
    threading.Thread(target=run, daemon=True).start()


def order_alert_text(order, items, method):
    lines = []
    for it in items[:12]:
        label = it.get('name', 'Item') + (f" ({it['size']})" if it.get('size') else '') + f" x{it.get('qty', 1)}"
        if it.get('mode') == 'rent': label += ' [RENT]'
        lines.append('- ' + label)
    how = {'COD': 'Cash on delivery', 'Online': 'Online payment'}.get(method, 'Pick up in store (QR)')
    return (f"New order #{order['id']} - Shreejee Fashion\nCustomer: {order['customer_name']}\nPhone: {order['customer_phone']}\n"
            f"Total: Rs {order['total']:.2f} ({how})\n" + '\n'.join(lines) + (f"\nAddress: {order['address']}" if order['address'] else ''))


# ---------------------------------------------------------------- login attempt limits
def _cutoff():
    return (datetime.now() - timedelta(minutes=LOGIN_WINDOW_MIN)).strftime('%Y-%m-%d %H:%M:%S')


def attempts_count(kind, ident):
    with db() as con:
        return con.execute('SELECT COUNT(*) FROM login_attempts WHERE kind=? AND ident=? AND created_at>=?', (kind, ident[:120], _cutoff())).fetchone()[0]


def attempts_record(kind, ident):
    with db() as con:
        con.execute('INSERT INTO login_attempts(kind,ident,ip,created_at) VALUES(?,?,?,?)', (kind, ident[:120], client_ip(), now()))
        con.execute("DELETE FROM login_attempts WHERE created_at < ?", ((datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S'),))


def attempts_clear(kind, ident):
    with db() as con:
        con.execute('DELETE FROM login_attempts WHERE kind=? AND ident=?', (kind, ident[:120]))


def attempts_locked(kind, ident, limit=None):
    """Minutes left on a lock, or 0. Locks per account name AND per IP address."""
    limit = limit or LOGIN_MAX_FAILS
    with db() as con:
        rows = con.execute('SELECT created_at FROM login_attempts WHERE kind=? AND ident=? AND created_at>=? ORDER BY created_at', (kind, ident[:120], _cutoff())).fetchall()
        ip_fails = con.execute('SELECT COUNT(*) FROM login_attempts WHERE kind=? AND ip=? AND created_at>=?', (kind, client_ip(), _cutoff())).fetchone()[0]
    if len(rows) >= limit or ip_fails >= limit * 5:
        oldest = rows[0]['created_at'] if rows else now()
        try:
            unlock = datetime.strptime(oldest, '%Y-%m-%d %H:%M:%S') + timedelta(minutes=LOGIN_WINDOW_MIN)
            return max(1, int((unlock - datetime.now()).total_seconds() // 60) + 1)
        except ValueError:
            return LOGIN_WINDOW_MIN
    return 0


# ---------------------------------------------------------------- Google sign-in
@app.route('/auth/google', methods=['POST'])
def auth_google():
    if not GOOGLE_CLIENT_ID:
        abort(404)
    credential = request.form.get('credential', '')[:4000]
    if attempts_locked('google', client_ip(), 10):
        flash('Too many sign-in attempts. Please wait a few minutes.', 'danger'); return redirect(url_for('login'))
    try:
        info = json.loads(_http_get('https://oauth2.googleapis.com/tokeninfo?id_token=' + urllib.parse.quote(credential)))
        ok = (info.get('aud') == GOOGLE_CLIENT_ID and info.get('iss') in ('accounts.google.com', 'https://accounts.google.com')
              and str(info.get('email_verified')).lower() == 'true' and info.get('email') and info.get('sub') and int(info.get('exp', 0)) > time.time())
    except Exception as exc:
        print('[Google sign-in failed]', exc); ok = False; info = {}
    if not ok:
        attempts_record('google', client_ip())
        flash('Google sign-in could not be verified. Please try again or use your password.', 'danger'); return redirect(url_for('login'))
    email = info['email'].strip().lower()
    with db() as con:
        user = con.execute('SELECT * FROM users WHERE google_sub=? OR lower(email)=?', (info['sub'], email)).fetchone()
        if user and user['role'] == 'admin':
            flash('Admin accounts must sign in with their ID and password.', 'warning'); return redirect(url_for('login'))
        if user and user['blocked']:
            flash('This account has been blocked. Contact the store administrator.', 'danger'); return redirect(url_for('login'))
        if user:
            if not user['google_sub']:
                con.execute('UPDATE users SET google_sub=? WHERE id=?', (info['sub'], user['id']))
        else:
            name = (info.get('name') or email.split('@')[0])[:100]
            con.execute('INSERT INTO users(name,email,phone,password_hash,role,verified,blocked,created_at,google_sub) VALUES(?,?,?,?,?,?,?,?,?)',
                        (name, email, '', generate_password_hash(secrets.token_urlsafe(24)), 'customer', 0, 0, now(), info['sub']))
        user = con.execute('SELECT * FROM users WHERE lower(email)=?', (email,)).fetchone()
    return complete_login(user)


# ---------------------------------------------------------------- phone OTP sign-in
def otp_enabled():
    return bool(FAST2SMS_API_KEY or OTP_DEV_MODE)


def _otp_hash(phone, code):
    return hashlib.sha256((app.secret_key if isinstance(app.secret_key, str) else app.secret_key.decode('utf-8', 'ignore')).encode() + f'|{phone}|{code}'.encode()).hexdigest()


def send_sms_otp(phone, code):
    if FAST2SMS_API_KEY:
        body = _http_get('https://www.fast2sms.com/dev/bulkV2?' + urllib.parse.urlencode({'authorization': FAST2SMS_API_KEY, 'route': 'otp', 'variables_values': code, 'numbers': phone}))
        if '"return":true' not in body.replace(' ', '').lower():
            raise RuntimeError('SMS provider refused: ' + body[:200])
    else:
        print(f'\n[OTP DEV MODE] Code for {phone}: {code}\n')


@app.route('/login/otp', methods=['GET', 'POST'])
def login_otp():
    if not otp_enabled():
        flash('Phone OTP sign-in is not set up yet. Please use your password.', 'info'); return redirect(url_for('login'))
    step, phone = 'phone', ''
    if request.method == 'POST':
        action = request.form.get('action', 'send')
        phone = phone_tail(request.form.get('phone'))
        if not phone:
            flash('Enter a valid 10-digit mobile number.', 'danger')
        elif action == 'send':
            with db() as con:
                recent = con.execute('SELECT created_at FROM otp_codes WHERE phone=? ORDER BY id DESC LIMIT 1', (phone,)).fetchone()
                hourly = con.execute('SELECT COUNT(*) FROM otp_codes WHERE phone=? AND created_at>=?', (phone, (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'))).fetchone()[0]
            if recent and (datetime.now() - datetime.strptime(recent['created_at'], '%Y-%m-%d %H:%M:%S')).total_seconds() < 60:
                flash('Please wait a minute before asking for another code.', 'warning'); step = 'code'
            elif hourly >= 5:
                flash('Too many codes requested for this number. Try again in an hour.', 'danger')
            else:
                code = f'{secrets.randbelow(1000000):06d}'
                try:
                    send_sms_otp(phone, code)
                    with db() as con:
                        con.execute('INSERT INTO otp_codes(phone,code_hash,expires_at,created_at) VALUES(?,?,?,?)', (phone, _otp_hash(phone, code), (datetime.now() + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S'), now()))
                    flash('We sent a 6-digit code to your phone. It works for 5 minutes.', 'success'); step = 'code'
                except Exception as exc:
                    print('[OTP send failed]', exc)
                    flash('Could not send the code right now. Please try again or use your password.', 'danger')
        else:
            step = 'code'
            wait = attempts_locked('otp', phone)
            code = re.sub(r'\D', '', request.form.get('code', ''))[:6]
            if wait:
                flash(f'Too many wrong codes. Try again in {wait} minute(s).', 'danger')
            else:
                with db() as con:
                    row = con.execute('SELECT * FROM otp_codes WHERE phone=? AND expires_at>=? ORDER BY id DESC LIMIT 1', (phone, now())).fetchone()
                if row and len(code) == 6 and secrets.compare_digest(row['code_hash'], _otp_hash(phone, code)):
                    attempts_clear('otp', phone)
                    with db() as con:
                        con.execute('DELETE FROM otp_codes WHERE phone=?', (phone,))
                        found = None
                        for cand in con.execute("SELECT * FROM users WHERE role='customer' AND phone<>'' AND lower(email)<>? ORDER BY id", (DEMO_CUSTOMER_EMAIL,)).fetchall():
                            if phone_tail(cand['phone']) == phone: found = cand; break
                        if found and found['blocked']:
                            flash('This account has been blocked. Contact the store administrator.', 'danger'); return redirect(url_for('login'))
                        if not found:
                            name = (request.form.get('name', '').strip() or ('Customer ' + phone[-4:]))[:100]
                            con.execute('INSERT INTO users(name,email,phone,password_hash,role,verified,blocked,created_at) VALUES(?,?,?,?,?,?,?,?)',
                                        (name, f'{phone}@phone.shreejee.local', phone, generate_password_hash(secrets.token_urlsafe(24)), 'customer', 0, 0, now()))
                            found = con.execute('SELECT * FROM users WHERE lower(email)=?', (f'{phone}@phone.shreejee.local',)).fetchone()
                    return complete_login(found)
                attempts_record('otp', phone)
                flash('That code is wrong or has expired.', 'danger')
    return render_template('login_otp.html', step=step, phone=phone, next_url=safe_local_path(request.args.get('next') or request.form.get('next'), ''))


# ---------------------------------------------------------------- delivery PIN codes
def delivery_settings():
    with db() as con:
        areas = con.execute('SELECT * FROM delivery_areas ORDER BY pin').fetchall()
        row = con.execute("SELECT value FROM site_settings WHERE key='delivery_restrict'").fetchone()
    return areas, bool(row and row['value'] == '1')


def pin_status(raw):
    pin = re.sub(r'\D', '', str(raw or ''))
    if not re.fullmatch(r'[1-9]\d{5}', pin):
        return {'ok': False, 'state': 'invalid', 'pin': pin, 'message': 'Enter a valid 6-digit PIN code.'}
    areas, restrict = delivery_settings()
    hit = next((r for r in areas if r['pin'] == pin), None)
    if hit:
        return {'ok': True, 'state': 'yes', 'pin': pin, 'message': f"Delivery is available to {pin}" + (f" ({hit['area']})." if hit['area'] else '.')}
    if restrict and areas:
        return {'ok': True, 'state': 'no', 'pin': pin, 'message': f'Sorry, we do not deliver to {pin} yet. You can still pick the order up in store.'}
    return {'ok': True, 'state': 'unknown', 'pin': pin, 'message': f'We will confirm delivery to {pin} after you order.'}


def check_address_pin(address):
    areas, restrict = delivery_settings()
    if not (restrict and areas):
        return True, ''
    pins = re.findall(r'(?<!\d)[1-9]\d{5}(?!\d)', address or '')
    allowed = {r['pin'] for r in areas}
    if not pins:
        return False, 'Please include your 6-digit PIN code in the delivery address.'
    if not any(p in allowed for p in pins):
        return False, f'Sorry, we do not deliver to PIN {pins[-1]} yet. You can pick the order up in store instead.'
    return True, ''


@app.route('/api/check-pin')
def api_check_pin():
    result = pin_status(request.args.get('pin', ''))
    if result['ok']: session['pin'] = result['pin']
    return jsonify(result)


@app.route('/admin/delivery', methods=['GET', 'POST'])
@admin_required
def admin_delivery():
    if request.method == 'POST':
        action = request.form.get('action', '')
        with db() as con:
            if action == 'add':
                added = 0
                for line in re.split(r'[\n;,]+', request.form.get('pins', '')):
                    m = re.match(r'\s*([1-9]\d{5})\s*[-:\u2013]?\s*(.*)$', line)
                    if m:
                        con.execute('INSERT INTO delivery_areas(pin,area,created_at) VALUES(?,?,?) ON CONFLICT(pin) DO UPDATE SET area=CASE WHEN excluded.area<>\'\' THEN excluded.area ELSE area END', (m.group(1), m.group(2).strip()[:60], now()))
                        added += 1
                flash(f'{added} PIN code(s) saved.' if added else 'Enter 6-digit PIN codes, e.g. 826001 Dhanbad.', 'success' if added else 'warning')
            elif action == 'delete':
                con.execute('DELETE FROM delivery_areas WHERE pin=?', (re.sub(r'\D', '', request.form.get('pin', '')),))
                flash('PIN code removed.', 'info')
            elif action == 'restrict':
                on = '1' if request.form.get('restrict') == '1' else '0'
                con.execute("INSERT INTO site_settings(key,value) VALUES('delivery_restrict',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (on,))
                flash('Only listed PIN codes can order delivery.' if on == '1' else 'Delivery is open to every PIN code (store confirms each order).', 'success')
        return redirect(url_for('admin_delivery'))
    areas, restrict = delivery_settings()
    return render_template('admin_delivery.html', areas=areas, restrict=restrict)


# ---------------------------------------------------------------- automatic database backups
def make_backup():
    import sqlite3 as _sq
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, 'store-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.db')
    src = _sq.connect(DB_PATH, timeout=30); dst = _sq.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith('store-') and f.endswith('.db'))
    for old in files[:-BACKUP_KEEP]:
        try: os.remove(os.path.join(BACKUP_DIR, old))
        except OSError: pass
    return dest


def list_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for f in sorted((f for f in os.listdir(BACKUP_DIR) if f.startswith('store-') and f.endswith('.db')), reverse=True):
        p = os.path.join(BACKUP_DIR, f)
        out.append({'name': f, 'size_kb': max(1, os.path.getsize(p) // 1024), 'when': datetime.fromtimestamp(os.path.getmtime(p)).strftime('%d %b %Y, %I:%M %p')})
    return out


def _backup_loop():
    while True:
        try:
            files = list_backups()
            newest = os.path.getmtime(os.path.join(BACKUP_DIR, files[0]['name'])) if files else 0
            if time.time() - newest > 24 * 3600:
                make_backup()
        except Exception as exc:
            print('[Backup failed]', exc)
        time.sleep(3600)


_backup_started = []


def start_backup_thread():
    if AUTO_BACKUP and not _backup_started:
        _backup_started.append(True)
        threading.Thread(target=_backup_loop, daemon=True, name='sf-backup').start()


@app.route('/admin/backup-now', methods=['POST'])
@admin_required
def admin_backup_now():
    try:
        make_backup(); flash('Backup saved in the "backups" folder.', 'success')
    except Exception as exc:
        flash(f'Backup failed: {exc}', 'danger')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/backups/<name>')
@admin_required
def admin_backup_download(name):
    if not re.fullmatch(r'store-\d{8}-\d{6}\.db', name):
        abort(404)
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=name)


# ---------------------------------------------------------------- Android app: tell it where the shop is right now
_published_urls = set()


def publish_tunnel_url(url):
    """Save the newest Cloudflare address so the Android app can find it.

    Always writes current_url.txt. If SF_GITHUB_TOKEN and SF_GIST_ID are set, also updates a private GitHub Gist
    (file shop.json) that the app reads on every start."""
    if url in _published_urls:
        return
    _published_urls.add(url)
    try:
        with open(os.path.join(APP_DIR, 'current_url.txt'), 'w', encoding='utf-8') as fh:
            fh.write(url + '\n')
    except OSError:
        pass
    token = os.environ.get('SF_GITHUB_TOKEN', '').strip(); gist = os.environ.get('SF_GIST_ID', '').strip()
    if not (token and gist):
        print('[App link] Set SF_GITHUB_TOKEN and SF_GIST_ID in .env so the Android app can find this address.')
        return
    def run():
        try:
            body = json.dumps({'files': {'shop.json': {'content': json.dumps({'url': url, 'updated': now()})}}}).encode('utf-8')
            req = urllib.request.Request('https://api.github.com/gists/' + gist, data=body, method='PATCH', headers={
                'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json', 'User-Agent': 'ShreejeeFashion'})
            urllib.request.urlopen(req, timeout=15).read()
            print('[App link] Android app address updated.')
        except Exception as exc:
            print('[App link] Could not update the Gist:', exc)
    threading.Thread(target=run, daemon=True).start()


app.jinja_env.globals.update(google_client_id=GOOGLE_CLIENT_ID, otp_ready=otp_enabled, colour_hex=colour_hex, saved_pin=lambda: session.get('pin', ''))


init_db()
start_backup_thread()

if __name__ == '__main__':
    # Double-click app.py: start Flask and a temporary Cloudflare Quick Tunnel.
    port = int(os.environ.get('PORT', '5000'))
    host = os.environ.get('HOST', '127.0.0.1')
    print('\n' + '=' * 62)
    print(' Shreejee Fashion is starting...')
    print(f' Local website: http://127.0.0.1:{port}')
    print(' Keep this window open while using the website.')
    print(' A temporary Cloudflare URL will appear below when ready.')
    print('=' * 62 + '\n')
    try:
        import threading, webbrowser
        tunnel_thread = threading.Thread(target=_start_cloudflare, args=(port,), daemon=True)
        tunnel_thread.start()
        # The local http address is intentionally NOT opened; the Cloudflare URL opens once it is ready.
    except Exception as exc:
        print('[Startup]', exc)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
