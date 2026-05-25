#!/usr/bin/env python3
"""
🏭 OMBOR BOSHQARUVI BOTI
Telegram Warehouse Management Bot
"""

import logging
import sqlite3
import os
import datetime
from calendar import monthrange
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── DATABASE ───────────────────────────────────────────────────────────────
DB_PATH = 'warehouse.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS items (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT UNIQUE NOT NULL,
        unit     TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id    INTEGER NOT NULL,
        type       TEXT NOT NULL,        -- 'IN' yoki 'OUT'
        quantity   REAL NOT NULL,
        note       TEXT,
        user_id    INTEGER,
        username   TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (item_id) REFERENCES items(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
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
               COALESCE(SUM(CASE WHEN t.type='IN'  THEN t.quantity ELSE 0 END),0) AS total_in,
               COALESCE(SUM(CASE WHEN t.type='OUT' THEN t.quantity ELSE 0 END),0) AS total_out,
               COALESCE(SUM(CASE WHEN t.type='IN'  THEN t.quantity ELSE -t.quantity END),0) AS balance
        FROM items i
        LEFT JOIN transactions t ON i.id = t.item_id
        GROUP BY i.id ORDER BY i.name
    ''').fetchall()
    conn.close()
    return rows

def get_item_balance(item_id):
    conn = get_db()
    row = conn.execute('''
        SELECT i.id, i.name, i.unit,
               COALESCE(SUM(CASE WHEN t.type='IN'  THEN t.quantity ELSE 0 END),0) AS total_in,
               COALESCE(SUM(CASE WHEN t.type='OUT' THEN t.quantity ELSE 0 END),0) AS total_out,
               COALESCE(SUM(CASE WHEN t.type='IN'  THEN t.quantity ELSE -t.quantity END),0) AS balance
        FROM items i
        LEFT JOIN transactions t ON i.id = t.item_id
        WHERE i.id = ?
        GROUP BY i.id
    ''', (item_id,)).fetchone()
    conn.close()
    return row

# ─── STATES ─────────────────────────────────────────────────────────────────
(
    ADD_NAME, ADD_UNIT,
    IN_SELECT, IN_QTY,
    OUT_SELECT, OUT_QTY,
    DEL_SELECT
) = range(7)

MONTH_UZ = ['', 'Yanvar', 'Fevral', 'Mart', 'Aprel', 'May', 'Iyun',
            'Iyul', 'Avgust', 'Sentabr', 'Oktabr', 'Noyabr', 'Dekabr']

def fmt(num): return f"{num:,.2f}".replace(',', ' ')
def uname(u): return f"@{u.username}" if u.username else u.first_name

# ─── /start  /help ──────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🏭 *OMBOR BOSHQARUVI BOTIGA XUSH KELIBSIZ!*\n\n"
        "📋 *Buyruqlar ro'yxati:*\n\n"
        "➕ /add\\_item — Yangi tovar qo'shish\n"
        "📦 /incoming — Yuk keldi (kirim)\n"
        "📤 /outgoing — Tovar ishlatildi (chiqim)\n"
        "📊 /stock — Joriy qoldiqlar\n"
        "📝 /history — So'nggi harakatlar\n"
        "📈 /daily — Kunlik hisobot\n"
        "📅 /monthly — Oylik hisobot\n"
        "🔔 /setup — Avtomatik hisobot sozlash\n"
        "❌ /cancel — Amalni bekor qilish\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🟢 Bot 24/7 rejimda ishlaydi!"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

# ─── ADD ITEM ────────────────────────────────────────────────────────────────
async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *Yangi tovar qo'shish*\n\n"
        "Tovar nomini kiriting:\n_(masalan: Sement, Temir, Yog', Qum...)_",
        parse_mode='Markdown'
    )
    return ADD_NAME

async def add_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 1:
        await update.message.reply_text("⚠️ Iltimos, nom kiriting.")
        return ADD_NAME
    context.user_data['new_name'] = name
    await update.message.reply_text(
        f"✅ Nom: *{name}*\n\n"
        "O'lchov birligini kiriting:\n"
        "_(kg, litr, dona, metr, qop, tonna, m², m³...)_",
        parse_mode='Markdown'
    )
    return ADD_UNIT

async def add_item_unit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    unit = update.message.text.strip()
    name = context.user_data['new_name']
    conn = get_db()
    try:
        conn.execute("INSERT INTO items (name, unit) VALUES (?, ?)", (name, unit))
        conn.commit()
        await update.message.reply_text(
            f"✅ *{name}* ({unit}) — omborga qo'shildi!\n\n"
            "📦 Kirim qilish uchun: /incoming\n"
            "📊 Qoldiqlarni ko'rish: /stock",
            parse_mode='Markdown'
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text(
            f"⚠️ *{name}* allaqachon mavjud!",
            parse_mode='Markdown'
        )
    finally:
        conn.close()
    return ConversationHandler.END

# ─── INCOMING ────────────────────────────────────────────────────────────────
async def incoming_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    items = conn.execute("SELECT id, name, unit FROM items ORDER BY name").fetchall()
    conn.close()
    if not items:
        await update.message.reply_text(
            "❌ Hali hech qanday tovar yo'q.\n➕ /add\\_item bilan qo'shing.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(f"📦 {i['name']} ({i['unit']})", callback_data=f"in_{i['id']}")]
        for i in items
    ]
    keyboard.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")])
    await update.message.reply_text(
        "📦 *Qaysi tovar keldi?*\nRo'yxatdan tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return IN_SELECT

async def incoming_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("❌ Bekor qilindi.")
        return ConversationHandler.END
    item_id = int(q.data.split('_')[1])
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    context.user_data.update({'item_id': item_id, 'item_name': item['name'],
                               'item_unit': item['unit'], 'tx_type': 'IN'})
    bal = get_item_balance(item_id)
    await q.edit_message_text(
        f"📦 *{item['name']}*\n"
        f"Joriy qoldiq: *{fmt(bal['balance'])} {item['unit']}*\n\n"
        f"Qancha keldi? ({item['unit']}):",
        parse_mode='Markdown'
    )
    return IN_QTY

async def incoming_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = float(update.message.text.replace(',', '.').replace(' ', ''))
        assert qty > 0
    except:
        await update.message.reply_text("⚠️ Iltimos, to'g'ri son kiriting (masalan: 50 yoki 12.5).")
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
        f"✅ *Kirim qilindi!*\n\n"
        f"📦 Tovar: *{d['item_name']}*\n"
        f"➕ Keldi: *{fmt(qty)} {d['item_unit']}*\n"
        f"📊 Yangi qoldiq: *{fmt(bal['balance'])} {d['item_unit']}*\n\n"
        f"👤 {uname(update.effective_user)}",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# ─── OUTGOING ────────────────────────────────────────────────────────────────
async def outgoing_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = get_all_balances()
    if not balances:
        await update.message.reply_text(
            "❌ Hali hech qanday tovar yo'q.\n➕ /add\\_item bilan qo'shing.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    keyboard = []
    for b in balances:
        bal_str = fmt(b['balance'])
        keyboard.append([InlineKeyboardButton(
            f"📤 {b['name']} (qoldiq: {bal_str} {b['unit']})",
            callback_data=f"out_{b['id']}"
        )])
    keyboard.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")])
    await update.message.reply_text(
        "📤 *Qaysi tovardan ishlatildi?*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return OUT_SELECT

async def outgoing_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("❌ Bekor qilindi.")
        return ConversationHandler.END
    item_id = int(q.data.split('_')[1])
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    bal = get_item_balance(item_id)
    context.user_data.update({'item_id': item_id, 'item_name': item['name'],
                               'item_unit': item['unit'], 'tx_type': 'OUT',
                               'balance': bal['balance']})
    await q.edit_message_text(
        f"📤 *{item['name']}*\n"
        f"Joriy qoldiq: *{fmt(bal['balance'])} {item['unit']}*\n\n"
        f"Qancha ishlatildi? ({item['unit']}):",
        parse_mode='Markdown'
    )
    return OUT_QTY

async def outgoing_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = float(update.message.text.replace(',', '.').replace(' ', ''))
        assert qty > 0
    except:
        await update.message.reply_text("⚠️ Iltimos, to'g'ri son kiriting.")
        return OUT_QTY

    d = context.user_data
    if qty > d['balance']:
        await update.message.reply_text(
            f"⚠️ *Yetarli qoldiq yo'q!*\n"
            f"Joriy qoldiq: *{fmt(d['balance'])} {d['item_unit']}*\n"
            f"Siz kiritdingiz: *{fmt(qty)} {d['item_unit']}*\n\n"
            "Qayta kiriting:",
            parse_mode='Markdown'
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

    low_warn = ""
    if bal['balance'] <= 0:
        low_warn = "\n\n🔴 *DIQQAT: Ombor bo'sh!*"
    elif bal['balance'] < (bal['total_in'] * 0.1):
        low_warn = "\n\n🟡 *Ogohlantirish: Qoldiq kam qoldi!*"

    await update.message.reply_text(
        f"✅ *Chiqim qilindi!*\n\n"
        f"📦 Tovar: *{d['item_name']}*\n"
        f"➖ Ishlatildi: *{fmt(qty)} {d['item_unit']}*\n"
        f"📊 Qolgan qoldiq: *{fmt(bal['balance'])} {d['item_unit']}*\n\n"
        f"👤 {uname(update.effective_user)}{low_warn}",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# ─── STOCK ───────────────────────────────────────────────────────────────────
async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = get_all_balances()
    if not balances:
        await update.message.reply_text(
            "❌ Hali hech qanday tovar yo'q.\n➕ /add\\_item bilan boshlang.",
            parse_mode='Markdown'
        )
        return

    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    text = f"📊 *OMBOR QOLDIQLARI*\n🕐 {now}\n\n"
    for b in balances:
        if b['balance'] <= 0:
            emoji = "🔴"
        elif b['total_in'] > 0 and b['balance'] < b['total_in'] * 0.15:
            emoji = "🟡"
        else:
            emoji = "🟢"
        text += f"{emoji} *{b['name']}*: `{fmt(b['balance'])} {b['unit']}`\n"

    text += "\n━━━━━━━━━━━━━━━\n🟢 Yaxshi  🟡 Kam qoldi  🔴 Tugadi"
    await update.message.reply_text(text, parse_mode='Markdown')

# ─── HISTORY ─────────────────────────────────────────────────────────────────
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    rows = conn.execute('''
        SELECT t.type, t.quantity, t.username, t.created_at, i.name, i.unit
        FROM transactions t
        JOIN items i ON t.item_id = i.id
        ORDER BY t.created_at DESC LIMIT 20
    ''').fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📝 Hali hech qanday harakat yo'q.")
        return

    text = "📝 *SO'NGI 20 TA HARAKAT:*\n\n"
    for r in rows:
        emoji = "📦➕" if r['type'] == 'IN' else "📤➖"
        sign  = "+" if r['type'] == 'IN' else "-"
        dt_   = r['created_at'][:16].replace('T', ' ')
        text += (f"{emoji} *{r['name']}*: {sign}{fmt(r['quantity'])} {r['unit']}\n"
                 f"   👤 {r['username'] or '—'}  |  🕐 {dt_}\n\n")
    await update.message.reply_text(text, parse_mode='Markdown')

# ─── DAILY REPORT ────────────────────────────────────────────────────────────
async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = build_daily_report()
    await update.message.reply_text(text, parse_mode='Markdown')

def build_daily_report(target_date=None):
    if target_date is None:
        target_date = datetime.date.today().isoformat()
    conn = get_db()
    rows = conn.execute('''
        SELECT t.type, t.quantity, t.username, i.name, i.unit
        FROM transactions t JOIN items i ON t.item_id = i.id
        WHERE date(t.created_at) = ?
        ORDER BY t.created_at
    ''', (target_date,)).fetchall()
    conn.close()

    text = f"📈 *KUNLIK HISOBOT*\n📅 {target_date}\n\n"
    ins  = [r for r in rows if r['type'] == 'IN']
    outs = [r for r in rows if r['type'] == 'OUT']

    if ins:
        text += "📦 *KIRIM:*\n"
        for r in ins:
            text += f"  ➕ {r['name']}: +{fmt(r['quantity'])} {r['unit']} ({r['username'] or '—'})\n"

    if outs:
        text += "\n📤 *CHIQIM:*\n"
        for r in outs:
            text += f"  ➖ {r['name']}: -{fmt(r['quantity'])} {r['unit']} ({r['username'] or '—'})\n"

    if not ins and not outs:
        text += "ℹ️ Bugun hech qanday harakat bo'lmadi.\n"

    text += "\n📊 *JORIY QOLDIQLAR:*\n"
    for b in get_all_balances():
        emoji = "🔴" if b['balance'] <= 0 else ("🟡" if b['total_in'] > 0 and b['balance'] < b['total_in'] * 0.15 else "🟢")
        text += f"  {emoji} {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    return text

# ─── MONTHLY REPORT ──────────────────────────────────────────────────────────
async def monthly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = build_monthly_report()
    await update.message.reply_text(text, parse_mode='Markdown')

def build_monthly_report(year=None, month=None):
    now = datetime.datetime.now()
    if year is None:  year  = now.year
    if month is None: month = now.month
    _, last = monthrange(year, month)
    m_start = f"{year}-{month:02d}-01"
    m_end   = f"{year}-{month:02d}-{last}"

    conn = get_db()
    rows = conn.execute('''
        SELECT i.name, i.unit,
               COALESCE(SUM(CASE WHEN t.type='IN'  THEN t.quantity ELSE 0 END),0) AS m_in,
               COALESCE(SUM(CASE WHEN t.type='OUT' THEN t.quantity ELSE 0 END),0) AS m_out
        FROM items i
        LEFT JOIN transactions t ON i.id = t.item_id
            AND date(t.created_at) BETWEEN ? AND ?
        GROUP BY i.id ORDER BY i.name
    ''', (m_start, m_end)).fetchall()
    conn.close()

    text = f"📅 *OYLIK HISOBOT*\n🗓 {MONTH_UZ[month]} {year}\n\n"
    any_data = False
    for r in rows:
        if r['m_in'] > 0 or r['m_out'] > 0:
            any_data = True
            net = r['m_in'] - r['m_out']
            text += (f"📦 *{r['name']}* ({r['unit']})\n"
                     f"   ➕ Kirim:  {fmt(r['m_in'])}\n"
                     f"   ➖ Chiqim: {fmt(r['m_out'])}\n"
                     f"   📊 Farq:   {fmt(net)}\n\n")
    if not any_data:
        text += "ℹ️ Bu oy hech qanday harakat yo'q.\n"

    text += "━━━━━━━━━━━━━━━\n📊 *UMUMIY QOLDIQLAR:*\n"
    for b in get_all_balances():
        emoji = "🔴" if b['balance'] <= 0 else "🟢"
        text += f"  {emoji} {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    return text

# ─── SETUP AUTO REPORT ───────────────────────────────────────────────────────
async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 ('report_chat_id', str(chat_id)))
    conn.commit()
    conn.close()

    for job in context.job_queue.get_jobs_by_name('auto_daily'):
        job.schedule_removal()

    # Har kuni soat 20:00 Toshkent (UTC+5) = 15:00 UTC
    context.job_queue.run_daily(
        auto_daily_job,
        time=datetime.time(hour=15, minute=0, tzinfo=datetime.timezone.utc),
        data=chat_id,
        name='auto_daily'
    )

    await update.message.reply_text(
        "✅ *Avtomatik hisobot sozlandi!*\n\n"
        "🔔 Har kuni soat *20:00* da (Toshkent vaqti)\n"
        "shu guruhga kunlik hisobot yuboriladi.\n\n"
        "📅 Oylik hisobot: har oy 1-kuni soat 09:00 da.",
        parse_mode='Markdown'
    )

async def auto_daily_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    text = build_daily_report()
    text = "🌙 *AVTOMATIK KUNLIK HISOBOT*\n\n" + text[text.index('\n')+1:]
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')

# ─── CANCEL ──────────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Amal bekor qilindi.")
    return ConversationHandler.END

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    init_db()
    TOKEN = os.environ.get('BOT_TOKEN')
    if not TOKEN:
        raise SystemExit("❌ BOT_TOKEN environment variable topilmadi!")

    app = Application.builder().token(TOKEN).build()

    # Conversations
    add_conv = ConversationHandler(
        entry_points=[CommandHandler('add_item', add_item_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_item_name)],
            ADD_UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_item_unit)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    in_conv = ConversationHandler(
        entry_points=[CommandHandler('incoming', incoming_start)],
        states={
            IN_SELECT: [CallbackQueryHandler(incoming_select, pattern=r'^(in_\d+|cancel)$')],
            IN_QTY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, incoming_qty)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    out_conv = ConversationHandler(
        entry_points=[CommandHandler('outgoing', outgoing_start)],
        states={
            OUT_SELECT: [CallbackQueryHandler(outgoing_select, pattern=r'^(out_\d+|cancel)$')],
            OUT_QTY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, outgoing_qty)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(CommandHandler('start',    start))
    app.add_handler(CommandHandler('help',     start))
    app.add_handler(CommandHandler('stock',    stock_command))
    app.add_handler(CommandHandler('history',  history_command))
    app.add_handler(CommandHandler('daily',    daily_report))
    app.add_handler(CommandHandler('monthly',  monthly_report))
    app.add_handler(CommandHandler('setup',    setup_command))
    app.add_handler(add_conv)
    app.add_handler(in_conv)
    app.add_handler(out_conv)

    # Restore saved job on restart
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='report_chat_id'").fetchone()
    conn.close()
    if row:
        app.job_queue.run_daily(
            auto_daily_job,
            time=datetime.time(hour=15, minute=0, tzinfo=datetime.timezone.utc),
            data=int(row['value']),
            name='auto_daily'
        )

    logger.info("🟢 Bot ishga tushdi!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
