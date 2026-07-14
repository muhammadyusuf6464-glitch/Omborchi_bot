import logging
import sqlite3
import os
import datetime
from calendar import monthrange
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# DB_PATH: Railway'da Volume ulab, uni masalan /data ga mount qiling va
# DB_PATH muhit o'zgaruvchisini /data/warehouse.db qilib bering.
# Shunda konteyner qayta yaratilganda ham malumotlar saqlanib qoladi.
DB_PATH = os.environ.get('DB_PATH', 'warehouse.db')
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

# GROUP_CHAT_ID: bot faqat shu guruh AZOLARIGA xizmat qilishi kerak bo'lsa,
# guruhning chat ID sini shu yerga kiriting (masalan: -1001234567890).
# Foydalanuvchi istalgan chatdan (guruh yoki shaxsiy) yozishi mumkin -
# bot uning shu guruhga azo ekanligini Telegram orqali tekshiradi.
# Bo'sh qoldirilsa - bu tekshiruv o'chiriladi.
GROUP_CHAT_ID = int(os.environ.get('GROUP_CHAT_ID', '0') or 0)

# ADMIN_IDS: qo'shimcha ravishda, faqat muayyan xodimlarga ruxsat berish kerak bo'lsa,
# ularning Telegram user_id larini shu yerga, vergul bilan kiriting.
# Bo'sh qoldirilsa - GROUP_CHAT_ID guruhidagi barcha azoga ruxsat beriladi.
ADMIN_IDS = set(int(x) for x in os.environ.get('ADMIN_IDS', '').split(',') if x.strip())


async def has_permission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Agar ADMIN_IDS sozlangan bo'lsa - faqat shu ro'yxatdagilarga ruxsat.
    if ADMIN_IDS:
        return update.effective_user.id in ADMIN_IDS
    # ADMIN_IDS bo'sh, GROUP_CHAT_ID ham bo'sh bo'lsa - hammaga ochiq.
    if not GROUP_CHAT_ID:
        return True
    # ADMIN_IDS bo'sh, lekin GROUP_CHAT_ID bor - foydalanuvchi shu guruhga
    # a'zo ekanligini tekshiramiz (shaxsiy chatdan yozgan bo'lsa ham ishlaydi).
    try:
        member = await context.bot.get_chat_member(GROUP_CHAT_ID, update.effective_user.id)
        return member.status in ("member", "administrator", "creator", "restricted")
    except Exception as e:
        logger.warning(f"Guruh azoligini tekshirishda xatolik: {e}")
        return False


async def deny_permission(update: Update):
    await update.message.reply_text(
        "Kechirasiz, sizda bu amalni bajarish uchun ruxsat yoq.\n"
        "Admin bilan boglaning."
    )


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        unit TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        quantity REAL NOT NULL,
        user_id INTEGER,
        username TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (item_id) REFERENCES items(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    )''')
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_all_balances():
    conn = get_db()
    rows = conn.execute('''
        SELECT i.id, i.name, i.unit,
        COALESCE(SUM(CASE WHEN t.type='IN' THEN t.quantity ELSE 0 END),0) AS total_in,
        COALESCE(SUM(CASE WHEN t.type='OUT' THEN t.quantity ELSE 0 END),0) AS total_out,
        COALESCE(SUM(CASE WHEN t.type='IN' THEN t.quantity ELSE -t.quantity END),0) AS balance
        FROM items i LEFT JOIN transactions t ON i.id = t.item_id
        GROUP BY i.id ORDER BY i.name
    ''').fetchall()
    conn.close()
    return rows


def get_item_balance(item_id):
    conn = get_db()
    row = conn.execute('''
        SELECT i.id, i.name, i.unit,
        COALESCE(SUM(CASE WHEN t.type='IN' THEN t.quantity ELSE 0 END),0) AS total_in,
        COALESCE(SUM(CASE WHEN t.type='OUT' THEN t.quantity ELSE 0 END),0) AS total_out,
        COALESCE(SUM(CASE WHEN t.type='IN' THEN t.quantity ELSE -t.quantity END),0) AS balance
        FROM items i LEFT JOIN transactions t ON i.id = t.item_id
        WHERE i.id=? GROUP BY i.id
    ''', (item_id,)).fetchone()
    conn.close()
    return row


# States
(ADD_NAME, ADD_UNIT,
 IN_SELECT, IN_QTY,
 OUT_SELECT, OUT_QTY,
 EDIT_SELECT, EDIT_CHOOSE, EDIT_VALUE,
 DEL_SELECT, DEL_CONFIRM) = range(11)

MONTHS = ['', 'Yanvar', 'Fevral', 'Mart', 'Aprel', 'May', 'Iyun', 'Iyul', 'Avgust', 'Sentabr', 'Oktabr', 'Noyabr', 'Dekabr']


def fmt(n):
    return f"{n:,.2f}".replace(',', ' ')


def uname(u):
    return f"@{u.username}" if u.username else u.first_name


BUTTONS = ["Kirim", "Chiqim", "Tovar qoshish", "Qoldiqlar",
           "Tarix", "Kunlik hisobot", "Oylik hisobot", "Sozlamalar",
           "Tovarni tahrirlash", "Tovarni ochirish"]


def main_kb():
    kb = [
        [KeyboardButton("Kirim"), KeyboardButton("Chiqim")],
        [KeyboardButton("Tovar qoshish"), KeyboardButton("Qoldiqlar")],
        [KeyboardButton("Tarix"), KeyboardButton("Kunlik hisobot")],
        [KeyboardButton("Oylik hisobot"), KeyboardButton("Sozlamalar")],
        [KeyboardButton("Tovarni tahrirlash"), KeyboardButton("Tovarni ochirish")],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


# START
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "OMBOR BOSHQARUVI BOTIGA XUSH KELIBSIZ!\n\n"
        "Pastdagi tugmalardan foydalaning.\n\n"
        "Bot 24/7 ishlaydi!",
        reply_markup=main_kb()
    )


# TOVAR QOSHISH
async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await has_permission(update, context):
        await deny_permission(update)
        return ConversationHandler.END
    await update.message.reply_text(
        "Yangi tovar nomi kiriting:\n(masalan: Sement, Temir, Yog, Qum)"
    )
    return ADD_NAME


async def add_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt in BUTTONS:
        await route_button(update, context)
        return ConversationHandler.END
    context.user_data['new_name'] = txt
    await update.message.reply_text(
        f"Nom: {txt}\n\nOlchov birligini kiriting:\n(kg, litr, dona, metr, qop, tonna)"
    )
    return ADD_UNIT


async def add_item_unit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    unit = update.message.text.strip()
    name = context.user_data['new_name']
    conn = get_db()
    try:
        conn.execute("INSERT INTO items (name, unit) VALUES (?,?)", (name, unit))
        conn.commit()
        await update.message.reply_text(
            f"OK! {name} ({unit}) omborga qoshildi!",
            reply_markup=main_kb()
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text(
            f"{name} allaqachon mavjud!",
            reply_markup=main_kb()
        )
    finally:
        conn.close()
    return ConversationHandler.END


# KIRIM
async def incoming_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await has_permission(update, context):
        await deny_permission(update)
        return ConversationHandler.END
    conn = get_db()
    items = conn.execute("SELECT id, name, unit FROM items ORDER BY name").fetchall()
    conn.close()
    if not items:
        await update.message.reply_text(
            "Hali tovar yoq.\nTovar qoshish tugmasini bosing.",
            reply_markup=main_kb()
        )
        return ConversationHandler.END
    kb = []
    for i in items:
        bal = get_item_balance(i['id'])
        kb.append([InlineKeyboardButton(
            f"{i['name']} | qoldiq: {fmt(bal['balance'])} {i['unit']}",
            callback_data=f"in_{i['id']}"
        )])
    kb.append([InlineKeyboardButton("Bekor qilish", callback_data="cancel")])
    await update.message.reply_text(
        "Qaysi tovarga kirim qilasiz?",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return IN_SELECT


async def incoming_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Bekor qilindi.")
        return ConversationHandler.END
    item_id = int(q.data.split('_')[1])
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    bal = get_item_balance(item_id)
    context.user_data.update({
        'item_id': item_id,
        'item_name': item['name'],
        'item_unit': item['unit']
    })
    await q.edit_message_text(
        f"Tovar: {item['name']}\n"
        f"Joriy qoldiq: {fmt(bal['balance'])} {item['unit']}\n\n"
        f"Qancha keldi? (faqat son kiriting, masalan: 50)"
    )
    return IN_QTY


async def incoming_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt in BUTTONS:
        await route_button(update, context)
        return ConversationHandler.END
    try:
        qty = float(txt.replace(',', '.').replace(' ', ''))
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Xato! Faqat musbat son kiriting.\nMasalan: 50 yoki 12.5"
        )
        return IN_QTY
    d = context.user_data
    conn = get_db()
    conn.execute(
        "INSERT INTO transactions (item_id, type, quantity, user_id, username) VALUES (?,?,?,?,?)",
        (d['item_id'], 'IN', qty, update.effective_user.id, uname(update.effective_user))
    )
    conn.commit()
    conn.close()
    bal = get_item_balance(d['item_id'])
    await update.message.reply_text(
        f"KIRIM QILINDI!\n\n"
        f"Tovar: {d['item_name']}\n"
        f"Qoshildi: +{fmt(qty)} {d['item_unit']}\n"
        f"Yangi qoldiq: {fmt(bal['balance'])} {d['item_unit']}\n\n"
        f"Kim: {uname(update.effective_user)}",
        reply_markup=main_kb()
    )
    return ConversationHandler.END


# CHIQIM
async def outgoing_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await has_permission(update, context):
        await deny_permission(update)
        return ConversationHandler.END
    balances = get_all_balances()
    if not balances:
        await update.message.reply_text(
            "Hali tovar yoq.",
            reply_markup=main_kb()
        )
        return ConversationHandler.END
    kb = []
    for b in balances:
        kb.append([InlineKeyboardButton(
            f"{b['name']} | qoldiq: {fmt(b['balance'])} {b['unit']}",
            callback_data=f"out_{b['id']}"
        )])
    kb.append([InlineKeyboardButton("Bekor qilish", callback_data="cancel")])
    await update.message.reply_text(
        "Qaysi tovardan chiqim qilasiz?",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return OUT_SELECT


async def outgoing_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Bekor qilindi.")
        return ConversationHandler.END
    item_id = int(q.data.split('_')[1])
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    bal = get_item_balance(item_id)
    context.user_data.update({
        'item_id': item_id,
        'item_name': item['name'],
        'item_unit': item['unit'],
        'balance': bal['balance']
    })
    await q.edit_message_text(
        f"Tovar: {item['name']}\n"
        f"Joriy qoldiq: {fmt(bal['balance'])} {item['unit']}\n\n"
        f"Qancha ishlatildi? (faqat son kiriting)"
    )
    return OUT_QTY


async def outgoing_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt in BUTTONS:
        await route_button(update, context)
        return ConversationHandler.END
    try:
        qty = float(txt.replace(',', '.').replace(' ', ''))
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Xato! Faqat musbat son kiriting.\nMasalan: 50 yoki 12.5"
        )
        return OUT_QTY
    d = context.user_data
    if qty > d['balance']:
        await update.message.reply_text(
            f"Yetarli qoldiq yoq!\n"
            f"Joriy qoldiq: {fmt(d['balance'])} {d['item_unit']}\n\n"
            f"Qayta kiriting:"
        )
        return OUT_QTY
    conn = get_db()
    conn.execute(
        "INSERT INTO transactions (item_id, type, quantity, user_id, username) VALUES (?,?,?,?,?)",
        (d['item_id'], 'OUT', qty, update.effective_user.id, uname(update.effective_user))
    )
    conn.commit()
    conn.close()
    bal = get_item_balance(d['item_id'])
    warn = ""
    if bal['balance'] <= 0:
        warn = "\n\nDIQQAT: Ombor bosh!"
    elif bal['total_in'] > 0 and bal['balance'] < bal['total_in'] * 0.1:
        warn = "\n\nOgohlantirish: Qoldiq kam qoldi!"
    await update.message.reply_text(
        f"CHIQIM QILINDI!\n\n"
        f"Tovar: {d['item_name']}\n"
        f"Ishlatildi: -{fmt(qty)} {d['item_unit']}\n"
        f"Qolgan: {fmt(bal['balance'])} {d['item_unit']}\n\n"
        f"Kim: {uname(update.effective_user)}{warn}",
        reply_markup=main_kb()
    )
    return ConversationHandler.END


# TOVARNI TAHRIRLASH
async def edit_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await has_permission(update, context):
        await deny_permission(update)
        return ConversationHandler.END
    conn = get_db()
    items = conn.execute("SELECT id, name, unit FROM items ORDER BY name").fetchall()
    conn.close()
    if not items:
        await update.message.reply_text("Hali tovar yoq.", reply_markup=main_kb())
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"{i['name']} ({i['unit']})", callback_data=f"edit_{i['id']}")] for i in items]
    kb.append([InlineKeyboardButton("Bekor qilish", callback_data="cancel")])
    await update.message.reply_text("Qaysi tovarni tahrirlaysiz?", reply_markup=InlineKeyboardMarkup(kb))
    return EDIT_SELECT


async def edit_item_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Bekor qilindi.")
        return ConversationHandler.END
    item_id = int(q.data.split('_')[1])
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    context.user_data.update({'edit_id': item_id, 'edit_name': item['name'], 'edit_unit': item['unit']})
    kb = [
        [InlineKeyboardButton("Nomini ozgartirish", callback_data="edit_name")],
        [InlineKeyboardButton("Olchov birligini ozgartirish", callback_data="edit_unit")],
        [InlineKeyboardButton("Bekor qilish", callback_data="cancel")],
    ]
    await q.edit_message_text(
        f"Tovar: {item['name']} ({item['unit']})\n\nNimani ozgartirish kerak?",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return EDIT_CHOOSE


async def edit_item_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Bekor qilindi.")
        return ConversationHandler.END
    context.user_data['edit_field'] = q.data
    if q.data == "edit_name":
        await q.edit_message_text(f"Yangi nom kiriting:\n(hozirgi: {context.user_data['edit_name']})")
    else:
        await q.edit_message_text(f"Yangi olchov birligini kiriting:\n(hozirgi: {context.user_data['edit_unit']})")
    return EDIT_VALUE


async def edit_item_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt in BUTTONS:
        await route_button(update, context)
        return ConversationHandler.END
    d = context.user_data
    conn = get_db()
    try:
        if d['edit_field'] == "edit_name":
            conn.execute("UPDATE items SET name=? WHERE id=?", (txt, d['edit_id']))
            msg = f"Tovar nomi ozgartirildi:\n{d['edit_name']} -> {txt}"
        else:
            conn.execute("UPDATE items SET unit=? WHERE id=?", (txt, d['edit_id']))
            msg = f"Olchov birligi ozgartirildi:\n{d['edit_unit']} -> {txt}"
        conn.commit()
        await update.message.reply_text(msg, reply_markup=main_kb())
    except sqlite3.IntegrityError:
        await update.message.reply_text("Bu nom allaqachon mavjud!", reply_markup=main_kb())
    finally:
        conn.close()
    return ConversationHandler.END


# TOVARNI OCHIRISH
async def del_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await has_permission(update, context):
        await deny_permission(update)
        return ConversationHandler.END
    conn = get_db()
    items = conn.execute("SELECT id, name, unit FROM items ORDER BY name").fetchall()
    conn.close()
    if not items:
        await update.message.reply_text("Hali tovar yoq.", reply_markup=main_kb())
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"X {i['name']} ({i['unit']})", callback_data=f"del_{i['id']}")] for i in items]
    kb.append([InlineKeyboardButton("Bekor qilish", callback_data="cancel")])
    await update.message.reply_text("Qaysi tovarni ochirish kerak?", reply_markup=InlineKeyboardMarkup(kb))
    return DEL_SELECT


async def del_item_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Bekor qilindi.")
        return ConversationHandler.END
    item_id = int(q.data.split('_')[1])
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    context.user_data['del_id'] = item_id
    context.user_data['del_name'] = item['name']
    kb = [
        [InlineKeyboardButton("Ha, ochirish", callback_data="del_confirm")],
        [InlineKeyboardButton("Yoq, bekor qilish", callback_data="cancel")],
    ]
    await q.edit_message_text(
        f"DIQQAT!\n\n{item['name']} tovari va unga tegishli BARCHA tarix "
        f"(kirim/chiqim yozuvlari) butunlay ochirib tashlanadi.\n\n"
        f"Bu amalni ortga qaytarib bolmaydi. Rostdan ham ochirasizmi?",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return DEL_CONFIRM


async def del_item_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Bekor qilindi.")
        return ConversationHandler.END
    item_id = context.user_data['del_id']
    name = context.user_data['del_name']
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE item_id=?", (item_id,))
    conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    await q.edit_message_text(f"{name} ombordan ochirildi!")
    return ConversationHandler.END


# QOLDIQLAR
async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = get_all_balances()
    if not balances:
        await update.message.reply_text("Hali tovar yoq. Tovar qoshish tugmasini bosing.", reply_markup=main_kb())
        return
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    text = f"OMBOR QOLDIQLARI\n{now}\n\n"
    for b in balances:
        if b['balance'] <= 0:
            status = "[BOSH]"
        elif b['total_in'] > 0 and b['balance'] < b['total_in'] * 0.15:
            status = "[KAM]"
        else:
            status = "[OK]"
        text += f"{status} {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    text += "\n[OK]=Yaxshi [KAM]=Kam qoldi [BOSH]=Tugadi"
    await update.message.reply_text(text, reply_markup=main_kb())


# TARIX
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    rows = conn.execute('''
        SELECT t.type, t.quantity, t.username, t.created_at, i.name, i.unit
        FROM transactions t JOIN items i ON t.item_id = i.id
        ORDER BY t.created_at DESC LIMIT 20
    ''').fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Hali hech qanday harakat yoq.", reply_markup=main_kb())
        return
    text = "SONGI 20 TA HARAKAT:\n\n"
    for r in rows:
        tip = "KIRIM" if r['type'] == 'IN' else "CHIQIM"
        sign = "+" if r['type'] == 'IN' else "-"
        dt_ = r['created_at'][:16].replace('T', ' ')
        text += f"{tip} {r['name']}: {sign}{fmt(r['quantity'])} {r['unit']}\n{r['username'] or '-'} | {dt_}\n\n"
    await update.message.reply_text(text, reply_markup=main_kb())


# HISOBOTLAR
def build_daily(date=None):
    if date is None:
        date = datetime.date.today().isoformat()
    conn = get_db()
    rows = conn.execute('''
        SELECT t.type, t.quantity, t.username, i.name, i.unit
        FROM transactions t JOIN items i ON t.item_id = i.id
        WHERE date(t.created_at)=? ORDER BY t.created_at
    ''', (date,)).fetchall()
    conn.close()
    text = f"KUNLIK HISOBOT\n{date}\n\n"
    ins = [r for r in rows if r['type'] == 'IN']
    outs = [r for r in rows if r['type'] == 'OUT']
    if ins:
        text += "KIRIM:\n"
        for r in ins:
            text += f" + {r['name']}: {fmt(r['quantity'])} {r['unit']} — Kim: {r['username'] or '-'}\n"
    if outs:
        text += "\nCHIQIM:\n"
        for r in outs:
            text += f" - {r['name']}: {fmt(r['quantity'])} {r['unit']} — Kim: {r['username'] or '-'}\n"
    if not ins and not outs:
        text += "Bugun harakat bolmadi.\n"
    text += "\nJORIY QOLDIQLAR:\n"
    for b in get_all_balances():
        text += f" {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    return text


def build_monthly(year=None, month=None):
    now = datetime.datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month
    _, last = monthrange(year, month)
    m_start = f"{year}-{month:02d}-01"
    m_end = f"{year}-{month:02d}-{last}"
    conn = get_db()
    rows = conn.execute('''
        SELECT i.name, i.unit,
        COALESCE(SUM(CASE WHEN t.type='IN' THEN t.quantity ELSE 0 END),0) AS m_in,
        COALESCE(SUM(CASE WHEN t.type='OUT' THEN t.quantity ELSE 0 END),0) AS m_out
        FROM items i LEFT JOIN transactions t ON i.id=t.item_id
        AND date(t.created_at) BETWEEN ? AND ?
        GROUP BY i.id ORDER BY i.name
    ''', (m_start, m_end)).fetchall()
    conn.close()
    text = f"OYLIK HISOBOT\n{MONTHS[month]} {year}\n\n"
    any_data = False
    for r in rows:
        if r['m_in'] > 0 or r['m_out'] > 0:
            any_data = True
            text += (f"{r['name']} ({r['unit']})\n"
                     f" Kirim: {fmt(r['m_in'])}\n"
                     f" Chiqim: {fmt(r['m_out'])}\n"
                     f" Farq: {fmt(r['m_in']-r['m_out'])}\n\n")
    if not any_data:
        text += "Bu oy harakat yoq.\n"
    text += "UMUMIY QOLDIQLAR:\n"
    for b in get_all_balances():
        text += f" {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    return text


async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_daily(), reply_markup=main_kb())


async def monthly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_monthly(), reply_markup=main_kb())


# SOZLAMALAR
async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", ('report_chat_id', str(chat_id)))
    conn.commit()
    conn.close()
    for job in context.job_queue.get_jobs_by_name('auto_daily'):
        job.schedule_removal()
    for job in context.job_queue.get_jobs_by_name('auto_backup'):
        job.schedule_removal()
    context.job_queue.run_daily(
        auto_daily_job,
        time=datetime.time(hour=15, minute=0, tzinfo=datetime.timezone.utc),
        data=chat_id, name='auto_daily'
    )
    context.job_queue.run_daily(
        backup_job,
        time=datetime.time(hour=16, minute=0, tzinfo=datetime.timezone.utc),
        data=chat_id, name='auto_backup'
    )
    await update.message.reply_text(
        "Avtomatik hisobot va zaxira nusxalash sozlandi!\n"
        "Har kuni soat 20:00 da hisobot, 21:00 da zaxira nusxa yuboriladi (Toshkent vaqti).",
        reply_markup=main_kb()
    )


async def auto_daily_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=context.job.data,
        text="AVTOMATIK KUNLIK HISOBOT\n\n" + build_daily()
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bekor qilindi.", reply_markup=main_kb())
    return ConversationHandler.END


# XATOLIKLARNI KUZATISH
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Botda xatolik yuz berdi:", exc_info=context.error)
    admin_chat = get_report_chat_id()
    if admin_chat:
        try:
            await context.bot.send_message(
                chat_id=admin_chat,
                text=f"BOTDA XATOLIK YUZ BERDI:\n\n{context.error}"
            )
        except Exception:
            logger.error("Admin'ga xatolik xabarini yuborib bolmadi.")


# AVTOMATIK ZAXIRA (BACKUP)
async def backup_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    try:
        with open(DB_PATH, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id, document=f,
                filename=f"warehouse_backup_{datetime.date.today().isoformat()}.db",
                caption="Kunlik avtomatik zaxira nusxasi."
            )
    except Exception as e:
        logger.error(f"Backup yuborishda xatolik: {e}")


def get_report_chat_id():
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='report_chat_id'").fetchone()
    conn.close()
    return int(row['value']) if row else None


async def route_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    if txt == "Qoldiqlar":
        await stock_command(update, context)
    elif txt == "Tarix":
        await history_command(update, context)
    elif txt == "Kunlik hisobot":
        await daily_report(update, context)
    elif txt == "Oylik hisobot":
        await monthly_report(update, context)
    elif txt == "Sozlamalar":
        await setup_command(update, context)


def main():
    init_db()
    TOKEN = os.environ.get('BOT_TOKEN')
    if not TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi!")

    app = Application.builder().token(TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler('add_item', add_item_start),
            MessageHandler(filters.Regex("^Tovar qoshish$"), add_item_start)
        ],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_item_name)],
            ADD_UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_item_unit)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    in_conv = ConversationHandler(
        entry_points=[
            CommandHandler('incoming', incoming_start),
            MessageHandler(filters.Regex("^Kirim$"), incoming_start)
        ],
        states={
            IN_SELECT: [CallbackQueryHandler(incoming_select, pattern=r'^(in_\d+|cancel)$')],
            IN_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, incoming_qty)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    out_conv = ConversationHandler(
        entry_points=[
            CommandHandler('outgoing', outgoing_start),
            MessageHandler(filters.Regex("^Chiqim$"), outgoing_start)
        ],
        states={
            OUT_SELECT: [CallbackQueryHandler(outgoing_select, pattern=r'^(out_\d+|cancel)$')],
            OUT_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, outgoing_qty)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    edit_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^Tovarni tahrirlash$"), edit_item_start)
        ],
        states={
            EDIT_SELECT: [CallbackQueryHandler(edit_item_select, pattern=r'^(edit_\d+|cancel)$')],
            EDIT_CHOOSE: [CallbackQueryHandler(edit_item_choose, pattern=r'^(edit_name|edit_unit|cancel)$')],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_item_value)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    del_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^Tovarni ochirish$"), del_item_start)
        ],
        states={
            DEL_SELECT: [CallbackQueryHandler(del_item_select, pattern=r'^(del_\d+|cancel)$')],
            DEL_CONFIRM: [CallbackQueryHandler(del_item_confirm, pattern=r'^(del_confirm|cancel)$')],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', start))
    app.add_handler(add_conv)
    app.add_handler(in_conv)
    app.add_handler(out_conv)
    app.add_handler(edit_conv)
    app.add_handler(del_conv)
    app.add_handler(MessageHandler(
        filters.Regex("^(Qoldiqlar|Tarix|Kunlik hisobot|Oylik hisobot|Sozlamalar)$"),
        route_button
    ))
    app.add_error_handler(error_handler)

    chat_id = get_report_chat_id()
    if chat_id:
        app.job_queue.run_daily(
            auto_daily_job,
            time=datetime.time(hour=15, minute=0, tzinfo=datetime.timezone.utc),
            data=chat_id, name='auto_daily'
        )
        app.job_queue.run_daily(
            backup_job,
            time=datetime.time(hour=16, minute=0, tzinfo=datetime.timezone.utc),
            data=chat_id, name='auto_backup'
        )

    logger.info("Bot ishga tushdi!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
