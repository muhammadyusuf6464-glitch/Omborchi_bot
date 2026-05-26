#!/usr/bin/env python3
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

DB_PATH = 'warehouse.db'

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
        note TEXT,
        user_id INTEGER,
        username TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (item_id) REFERENCES items(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
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

# STATES
(ADD_NAME, ADD_UNIT, IN_SELECT, IN_QTY, OUT_SELECT, OUT_QTY) = range(6)

MONTH_UZ = ['','Yanvar','Fevral','Mart','Aprel','May','Iyun','Iyul','Avgust','Sentabr','Oktabr','Noyabr','Dekabr']

def fmt(num): return f"{num:,.2f}".replace(',', ' ')
def uname(u): return f"@{u.username}" if u.username else u.first_name

# Ana klaviatura
def main_keyboard():
    keyboard = [
        [KeyboardButton("📦 Kirim"), KeyboardButton("📤 Chiqim")],
        [KeyboardButton("➕ Tovar qo'shish"), KeyboardButton("📊 Qoldiqlar")],
        [KeyboardButton("📝 Tarix"), KeyboardButton("📈 Kunlik hisobot")],
        [KeyboardButton("📅 Oylik hisobot"), KeyboardButton("🔔 Sozlamalar")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🏭 *OMBOR BOSHQARUVI BOTIGA XUSH KELIBSIZ!*\n\n"
        "Pastdagi tugmalardan foydalaning 👇\n\n"
        "🟢 Bot 24/7 rejimda ishlaydi!"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_keyboard())

# Xabar orqali tugmalarni ushlash
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📦 Kirim":
        await incoming_start(update, context)
        return IN_SELECT
    elif text == "📤 Chiqim":
        await outgoing_start(update, context)
        return OUT_SELECT
    elif text == "➕ Tovar qo'shish":
        await add_item_start(update, context)
        return ADD_NAME
    elif text == "📊 Qoldiqlar":
        await stock_command(update, context)
    elif text == "📝 Tarix":
        await history_command(update, context)
    elif text == "📈 Kunlik hisobot":
        await daily_report(update, context)
    elif text == "📅 Oylik hisobot":
        await monthly_report(update, context)
    elif text == "🔔 Sozlamalar":
        await setup_command(update, context)
    return ConversationHandler.END

# TOVAR QO'SHISH
async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *Yangi tovar qo'shish*\n\nTovar nomini kiriting:\n_(masalan: Sement, Temir, Yog', Qum...)_",
        parse_mode='Markdown'
    )
    return ADD_NAME

async def add_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in ["📦 Kirim","📤 Chiqim","➕ Tovar qo'shish","📊 Qoldiqlar","📝 Tarix","📈 Kunlik hisobot","📅 Oylik hisobot","🔔 Sozlamalar"]:
        await handle_buttons(update, context)
        return ConversationHandler.END
    name = update.message.text.strip()
    context.user_data['new_name'] = name
    await update.message.reply_text(
        f"✅ Nom: *{name}*\n\nO'lchov birligini kiriting:\n_(kg, litr, dona, metr, qop, tonna...)_",
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
            f"✅ *{name}* ({unit}) — omborga qo'shildi!",
            parse_mode='Markdown', reply_markup=main_keyboard()
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"⚠️ *{name}* allaqachon mavjud!", parse_mode='Markdown', reply_markup=main_keyboard())
    finally:
        conn.close()
    return ConversationHandler.END

# KIRIM
async def incoming_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    items = conn.execute("SELECT id, name, unit FROM items ORDER BY name").fetchall()
    conn.close()
    if not items:
        await update.message.reply_text("❌ Hali hech qanday tovar yo'q.\n➕ *Tovar qo'shish* tugmasini bosing.", parse_mode='Markdown')
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(f"📦 {i['name']} ({i['unit']})", callback_data=f"in_{i['id']}")] for i in items]
    keyboard.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")])
    await update.message.reply_text("📦 *Qaysi tovar keldi?*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
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
    context.user_data.update({'item_id': item_id, 'item_name': item['name'], 'item_unit': item['unit']})
    bal = get_item_balance(item_id)
    await q.edit_message_text(
        f"📦 *{item['name']}*\nJoriy qoldiq: *{fmt(bal['balance'])} {item['unit']}*\n\nQancha keldi? ({item['unit']}):",
        parse_mode='Markdown'
    )
    return IN_QTY

async def incoming_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = float(update.message.text.replace(',', '.').replace(' ', ''))
        assert qty > 0
    except:
        await update.message.reply_text("⚠️ To'g'ri son kiriting (masalan: 50 yoki 12.5).")
        return IN_QTY
    d = context.user_data
    conn = get_db()
    conn.execute("INSERT INTO transactions (item_id, type, quantity, user_id, username) VALUES (?,?,?,?,?)",
                 (d['item_id'], 'IN', qty, update.effective_user.id, uname(update.effective_user)))
    conn.commit(); conn.close()
    bal = get_item_balance(d['item_id'])
    await update.message.reply_text(
        f"✅ *Kirim qilindi!*\n\n📦 Tovar: *{d['item_name']}*\n➕ Keldi: *{fmt(qty)} {d['item_unit']}*\n📊 Yangi qoldiq: *{fmt(bal['balance'])} {d['item_unit']}*\n\n👤 {uname(update.effective_user)}",
        parse_mode='Markdown', reply_markup=main_keyboard()
    )
    return ConversationHandler.END

# CHIQIM
async def outgoing_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = get_all_balances()
    if not balances:
        await update.message.reply_text("❌ Hali hech qanday tovar yo'q.", parse_mode='Markdown')
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(f"📤 {b['name']} (qoldiq: {fmt(b['balance'])} {b['unit']})", callback_data=f"out_{b['id']}")] for b in balances]
    keyboard.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")])
    await update.message.reply_text("📤 *Qaysi tovardan ishlatildi?*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
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
    context.user_data.update({'item_id': item_id, 'item_name': item['name'], 'item_unit': item['unit'], 'balance': bal['balance']})
    await q.edit_message_text(
        f"📤 *{item['name']}*\nJoriy qoldiq: *{fmt(bal['balance'])} {item['unit']}*\n\nQancha ishlatildi? ({item['unit']}):",
        parse_mode='Markdown'
    )
    return OUT_QTY

async def outgoing_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = float(update.message.text.replace(',', '.').replace(' ', ''))
        assert qty > 0
    except:
        await update.message.reply_text("⚠️ To'g'ri son kiriting.")
        return OUT_QTY
    d = context.user_data
    if qty > d['balance']:
        await update.message.reply_text(
            f"⚠️ *Yetarli qoldiq yo'q!*\nJoriy qoldiq: *{fmt(d['balance'])} {d['item_unit']}*\n\nQayta kiriting:",
            parse_mode='Markdown'
        )
        return OUT_QTY
    conn = get_db()
    conn.execute("INSERT INTO transactions (item_id, type, quantity, user_id, username) VALUES (?,?,?,?,?)",
                 (d['item_id'], 'OUT', qty, update.effective_user.id, uname(update.effective_user)))
    conn.commit(); conn.close()
    bal = get_item_balance(d['item_id'])
    warn = ""
    if bal['balance'] <= 0: warn = "\n\n🔴 *DIQQAT: Ombor bo'sh!*"
    elif bal['total_in'] > 0 and bal['balance'] < bal['total_in'] * 0.1: warn = "\n\n🟡 *Ogohlantirish: Qoldiq kam qoldi!*"
    await update.message.reply_text(
        f"✅ *Chiqim qilindi!*\n\n📦 Tovar: *{d['item_name']}*\n➖ Ishlatildi: *{fmt(qty)} {d['item_unit']}*\n📊 Qolgan qoldiq: *{fmt(bal['balance'])} {d['item_unit']}*\n\n👤 {uname(update.effective_user)}{warn}",
        parse_mode='Markdown', reply_markup=main_keyboard()
    )
    return ConversationHandler.END

# QOLDIQLAR
async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = get_all_balances()
    if not balances:
        await update.message.reply_text("❌ Hali hech qanday tovar yo'q.\n➕ *Tovar qo'shish* tugmasini bosing.", parse_mode='Markdown')
        return
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    text = f"📊 *OMBOR QOLDIQLARI*\n🕐 {now}\n\n"
    for b in balances:
        emoji = "🔴" if b['balance'] <= 0 else ("🟡" if b['total_in'] > 0 and b['balance'] < b['total_in'] * 0.15 else "🟢")
        text += f"{emoji} *{b['name']}*: `{fmt(b['balance'])} {b['unit']}`\n"
    text += "\n━━━━━━━━━━━━━━━\n🟢 Yaxshi  🟡 Kam qoldi  🔴 Tugadi"
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_keyboard())

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
        await update.message.reply_text("📝 Hali hech qanday harakat yo'q.")
        return
    text = "📝 *SO'NGI 20 TA HARAKAT:*\n\n"
    for r in rows:
        emoji = "📦➕" if r['type'] == 'IN' else "📤➖"
        sign = "+" if r['type'] == 'IN' else "-"
        dt_ = r['created_at'][:16].replace('T', ' ')
        text += f"{emoji} *{r['name']}*: {sign}{fmt(r['quantity'])} {r['unit']}\n   👤 {r['username'] or '—'}  |  🕐 {dt_}\n\n"
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_keyboard())

# KUNLIK HISOBOT
async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_daily_report(), parse_mode='Markdown', reply_markup=main_keyboard())

def build_daily_report(target_date=None):
    if target_date is None: target_date = datetime.date.today().isoformat()
    conn = get_db()
    rows = conn.execute('''
        SELECT t.type, t.quantity, t.username, i.name, i.unit
        FROM transactions t JOIN items i ON t.item_id = i.id
        WHERE date(t.created_at) = ? ORDER BY t.created_at
    ''', (target_date,)).fetchall()
    conn.close()
    text = f"📈 *KUNLIK HISOBOT*\n📅 {target_date}\n\n"
    ins = [r for r in rows if r['type'] == 'IN']
    outs = [r for r in rows if r['type'] == 'OUT']
    if ins:
        text += "📦 *KIRIM:*\n"
        for r in ins: text += f"  ➕ {r['name']}: +{fmt(r['quantity'])} {r['unit']} ({r['username'] or '—'})\n"
    if outs:
        text += "\n📤 *CHIQIM:*\n"
        for r in outs: text += f"  ➖ {r['name']}: -{fmt(r['quantity'])} {r['unit']} ({r['username'] or '—'})\n"
    if not ins and not outs: text += "ℹ️ Bugun hech qanday harakat bo'lmadi.\n"
    text += "\n📊 *JORIY QOLDIQLAR:*\n"
    for b in get_all_balances():
        emoji = "🔴" if b['balance'] <= 0 else ("🟡" if b['total_in'] > 0 and b['balance'] < b['total_in'] * 0.15 else "🟢")
        text += f"  {emoji} {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    return text

# OYLIK HISOBOT
async def monthly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_monthly_report(), parse_mode='Markdown', reply_markup=main_keyboard())

def build_monthly_report(year=None, month=None):
    now = datetime.datetime.now()
    if year is None: year = now.year
    if month is None: month = now.month
    _, last = monthrange(year, month)
    m_start = f"{year}-{month:02d}-01"
    m_end = f"{year}-{month:02d}-{last}"
    conn = get_db()
    rows = conn.execute('''
        SELECT i.name, i.unit,
               COALESCE(SUM(CASE WHEN t.type='IN' THEN t.quantity ELSE 0 END),0) AS m_in,
               COALESCE(SUM(CASE WHEN t.type='OUT' THEN t.quantity ELSE 0 END),0) AS m_out
        FROM items i
        LEFT JOIN transactions t ON i.id = t.item_id AND date(t.created_at) BETWEEN ? AND ?
        GROUP BY i.id ORDER BY i.name
    ''', (m_start, m_end)).fetchall()
    conn.close()
    text = f"📅 *OYLIK HISOBOT*\n🗓 {MONTH_UZ[month]} {year}\n\n"
    any_data = False
    for r in rows:
        if r['m_in'] > 0 or r['m_out'] > 0:
            any_data = True
            text += f"📦 *{r['name']}* ({r['unit']})\n   ➕ Kirim:  {fmt(r['m_in'])}\n   ➖ Chiqim: {fmt(r['m_out'])}\n   📊 Farq:   {fmt(r['m_in']-r['m_out'])}\n\n"
    if not any_data: text += "ℹ️ Bu oy hech qanday harakat yo'q.\n"
    text += "━━━━━━━━━━━━━━━\n📊 *UMUMIY QOLDIQLAR:*\n"
    for b in get_all_balances():
        emoji = "🔴" if b['balance'] <= 0 else "🟢"
        text += f"  {emoji} {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    return text

# SOZLAMALAR
async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ('report_chat_id', str(chat_id)))
    conn.commit(); conn.close()
    for job in context.job_queue.get_jobs_by_name('auto_daily'):
        job.schedule_removal()
    context.job_queue.run_daily(
        auto_daily_job,
        time=datetime.time(hour=15, minute=0, tzinfo=datetime.timezone.utc),
        data=chat_id, name='auto_daily'
    )
    await update.message.reply_text(
        "✅ *Avtomatik hisobot sozlandi!*\n\n🔔 Har kuni soat *20:00* da (Toshkent vaqti)\nshu guruhga kunlik hisobot yuboriladi.",
        parse_mode='Markdown', reply_markup=main_keyboard()
    )

async def auto_daily_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    text = "🌙 *AVTOMATIK KUNLIK HISOBOT*\n\n" + build_daily_report()
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Amal bekor qilindi.", reply_markup=main_keyboard())
    return ConversationHandler.END

def main():
    init_db()
    TOKEN = os.environ.get('BOT_TOKEN')
    if not TOKEN:
        raise SystemExit("❌ BOT_TOKEN topilmadi!")

    app = Application.builder().token(TOKEN).build()

    # Conversation handlers
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler('add_item', add_item_start),
            MessageHandler(filters.Regex("^➕ Tovar qo'shish$"), add_item_start)
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
            MessageHandler(filters.Regex("^📦 Kirim$"), incoming_start)
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
            MessageHandler(filters.Regex("^📤 Chiqim$"), outgoing_start)
        ],
        states={
            OUT_SELECT: [CallbackQueryHandler(outgoing_select, pattern=r'^(out_\d+|cancel)$')],
            OUT_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, outgoing_qty)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', start))
    app.add_handler(add_conv)
    app.add_handler(in_conv)
    app.add_handler(out_conv)
    app.add_handler(CommandHandler('stock', stock_command))
    app.add_handler(CommandHandler('history', history_command))
    app.add_handler(CommandHandler('daily', daily_report))
    app.add_handler(CommandHandler('monthly', monthly_report))
    app.add_handler(CommandHandler('setup', setup_command))
    app.add_handler(MessageHandler(
        filters.Regex("^(📊 Qoldiqlar|📝 Tarix|📈 Kunlik hisobot|📅 Oylik hisobot|🔔 Sozlamalar)$"),
        handle_buttons
    ))

    # Restart da saqlangan job
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='report_chat_id'").fetchone()
    conn.close()
    if row:
        app.job_queue.run_daily(
            auto_daily_job,
            time=datetime.time(hour=15, minute=0, tzinfo=datetime.timezone.utc),
            data=int(row['value']), name='auto_daily'
        )

    logger.info("🟢 Bot ishga tushdi!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
