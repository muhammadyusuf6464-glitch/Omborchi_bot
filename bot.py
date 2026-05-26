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

ADD_NAME, ADD_UNIT, IN_SELECT, IN_QTY, OUT_SELECT, OUT_QTY = range(6)
MONTHS = ['','Yanvar','Fevral','Mart','Aprel','May','Iyun','Iyul','Avgust','Sentabr','Oktabr','Noyabr','Dekabr']

def fmt(n): return f"{n:,.2f}".replace(',', ' ')
def uname(u): return f"@{u.username}" if u.username else u.first_name

def main_kb():
    kb = [
        [KeyboardButton("Kirim"), KeyboardButton("Chiqim")],
        [KeyboardButton("Tovar qoshish"), KeyboardButton("Qoldiqlar")],
        [KeyboardButton("Tarix"), KeyboardButton("Kunlik hisobot")],
        [KeyboardButton("Oylik hisobot"), KeyboardButton("Sozlamalar")],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "OMBOR BOSHQARUVI BOTIGA XUSH KELIBSIZ!\n\nPastdagi tugmalardan foydalaning.\n\nBot 24/7 ishlaydi!",
        reply_markup=main_kb()
    )

async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Yangi tovar nomi kiriting:\n(masalan: Sement, Temir, Yog, Qum)")
    return ADD_NAME

async def add_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt in ["Kirim","Chiqim","Tovar qoshish","Qoldiqlar","Tarix","Kunlik hisobot","Oylik hisobot","Sozlamalar"]:
        await handle_buttons(update, context)
        return ConversationHandler.END
    context.user_data['new_name'] = txt
    await update.message.reply_text(f"Nom: {txt}\n\nOlchov birligini kiriting:\n(kg, litr, dona, metr, qop, tonna)")
    return ADD_UNIT

async def add_item_unit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    unit = update.message.text.strip()
    name = context.user_data['new_name']
    conn = get_db()
    try:
        conn.execute("INSERT INTO items (name, unit) VALUES (?,?)", (name, unit))
        conn.commit()
        await update.message.reply_text(f"OK! {name} ({unit}) omborga qoshildi!", reply_markup=main_kb())
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"{name} allaqachon mavjud!", reply_markup=main_kb())
    finally:
        conn.close()
    return ConversationHandler.END

async def incoming_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    items = conn.execute("SELECT id, name, unit FROM items ORDER BY name").fetchall()
    conn.close()
    if not items:
        await update.message.reply_text("Hali tovar yoq. Tovar qoshish tugmasini bosing.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"{i['name']} ({i['unit']})", callback_data=f"in_{i['id']}")] for i in items]
    kb.append([InlineKeyboardButton("Bekor qilish", callback_data="cancel")])
    await update.message.reply_text("Qaysi tovar keldi?", reply_markup=InlineKeyboardMarkup(kb))
    return IN_SELECT

async def incoming_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Bekor qilindi.")
        return ConversationHandler.END
    item_id = int(q.data.split('_')[1])
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    context.user_data.update({'item_id': item_id, 'item_name': item['name'], 'item_unit': item['unit']})
    bal = get_item_balance(item_id)
    await q.edit_message_text(f"{item['name']}\nJoriy qoldiq: {fmt(bal['balance'])} {item['unit']}\n\nQancha keldi? ({item['unit']}):")
    return IN_QTY

async def incoming_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = float(update.message.text.replace(',', '.').replace(' ', ''))
        assert qty > 0
    except:
        await update.message.reply_text("Togri son kiriting (masalan: 50 yoki 12.5).")
        return IN_QTY
    d = context.user_data
    conn = get_db()
    conn.execute("INSERT INTO transactions (item_id, type, quantity, user_id, username) VALUES (?,?,?,?,?)",
                 (d['item_id'], 'IN', qty, update.effective_user.id, uname(update.effective_user)))
    conn.commit(); conn.close()
    bal = get_item_balance(d['item_id'])
    await update.message.reply_text(
        f"KIRIM QILINDI!\n\nTovar: {d['item_name']}\nKeldi: +{fmt(qty)} {d['item_unit']}\nYangi qoldiq: {fmt(bal['balance'])} {d['item_unit']}\n\nKim: {uname(update.effective_user)}",
        reply_markup=main_kb()
    )
    return ConversationHandler.END

async def outgoing_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = get_all_balances()
    if not balances:
        await update.message.reply_text("Hali tovar yoq.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"{b['name']} (qoldiq: {fmt(b['balance'])} {b['unit']})", callback_data=f"out_{b['id']}")] for b in balances]
    kb.append([InlineKeyboardButton("Bekor qilish", callback_data="cancel")])
    await update.message.reply_text("Qaysi tovardan ishlatildi?", reply_markup=InlineKeyboardMarkup(kb))
    return OUT_SELECT

async def outgoing_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Bekor qilindi.")
        return ConversationHandler.END
    item_id = int(q.data.split('_')[1])
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    bal = get_item_balance(item_id)
    context.user_data.update({'item_id': item_id, 'item_name': item['name'], 'item_unit': item['unit'], 'balance': bal['balance']})
    await q.edit_message_text(f"{item['name']}\nJoriy qoldiq: {fmt(bal['balance'])} {item['unit']}\n\nQancha ishlatildi? ({item['unit']}):")
    return OUT_QTY

async def outgoing_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = float(update.message.text.replace(',', '.').replace(' ', ''))
        assert qty > 0
    except:
        await update.message.reply_text("Togri son kiriting.")
        return OUT_QTY
    d = context.user_data
    if qty > d['balance']:
        await update.message.reply_text(f"Yetarli qoldiq yoq!\nJoriy qoldiq: {fmt(d['balance'])} {d['item_unit']}\n\nQayta kiriting:")
        return OUT_QTY
    conn = get_db()
    conn.execute("INSERT INTO transactions (item_id, type, quantity, user_id, username) VALUES (?,?,?,?,?)",
                 (d['item_id'], 'OUT', qty, update.effective_user.id, uname(update.effective_user)))
    conn.commit(); conn.close()
    bal = get_item_balance(d['item_id'])
    warn = ""
    if bal['balance'] <= 0: warn = "\n\nDIQQAT: Ombor bosh!"
    elif bal['total_in'] > 0 and bal['balance'] < bal['total_in'] * 0.1: warn = "\n\nOgohlantirish: Qoldiq kam qoldi!"
    await update.message.reply_text(
        f"CHIQIM QILINDI!\n\nTovar: {d['item_name']}\nIshlatildi: -{fmt(qty)} {d['item_unit']}\nQolgan qoldiq: {fmt(bal['balance'])} {d['item_unit']}\n\nKim: {uname(update.effective_user)}{warn}",
        reply_markup=main_kb()
    )
    return ConversationHandler.END

async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = get_all_balances()
    if not balances:
        await update.message.reply_text("Hali tovar yoq. Tovar qoshish tugmasini bosing.")
        return
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    text = f"OMBOR QOLDIQLARI\n{now}\n\n"
    for b in balances:
        status = "BOSh" if b['balance'] <= 0 else ("KAM" if b['total_in'] > 0 and b['balance'] < b['total_in'] * 0.15 else "OK")
        text += f"[{status}] {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    await update.message.reply_text(text, reply_markup=main_kb())

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    rows = conn.execute('''
        SELECT t.type, t.quantity, t.username, t.created_at, i.name, i.unit
        FROM transactions t JOIN items i ON t.item_id = i.id
        ORDER BY t.created_at DESC LIMIT 20
    ''').fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Hali hech qanday harakat yoq.")
        return
    text = "SONGI 20 TA HARAKAT:\n\n"
    for r in rows:
        tip = "KIRIM" if r['type'] == 'IN' else "CHIQIM"
        sign = "+" if r['type'] == 'IN' else "-"
        dt_ = r['created_at'][:16].replace('T', ' ')
        text += f"{tip} {r['name']}: {sign}{fmt(r['quantity'])} {r['unit']}\n{r['username'] or '-'} | {dt_}\n\n"
    await update.message.reply_text(text, reply_markup=main_kb())

def build_daily(date=None):
    if date is None: date = datetime.date.today().isoformat()
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
        for r in ins: text += f"  + {r['name']}: {fmt(r['quantity'])} {r['unit']} ({r['username'] or '-'})\n"
    if outs:
        text += "\nCHIQIM:\n"
        for r in outs: text += f"  - {r['name']}: {fmt(r['quantity'])} {r['unit']} ({r['username'] or '-'})\n"
    if not ins and not outs: text += "Bugun harakat bolmadi.\n"
    text += "\nJORIY QOLDIQLAR:\n"
    for b in get_all_balances():
        status = "BOSh" if b['balance'] <= 0 else "OK"
        text += f"  [{status}] {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    return text

def build_monthly(year=None, month=None):
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
        FROM items i LEFT JOIN transactions t ON i.id=t.item_id AND date(t.created_at) BETWEEN ? AND ?
        GROUP BY i.id ORDER BY i.name
    ''', (m_start, m_end)).fetchall()
    conn.close()
    text = f"OYLIK HISOBOT\n{MONTHS[month]} {year}\n\n"
    any_data = False
    for r in rows:
        if r['m_in'] > 0 or r['m_out'] > 0:
            any_data = True
            text += f"{r['name']} ({r['unit']})\n  Kirim: {fmt(r['m_in'])}\n  Chiqim: {fmt(r['m_out'])}\n  Farq: {fmt(r['m_in']-r['m_out'])}\n\n"
    if not any_data: text += "Bu oy harakat yoq.\n"
    text += "UMUMIY QOLDIQLAR:\n"
    for b in get_all_balances():
        text += f"  {b['name']}: {fmt(b['balance'])} {b['unit']}\n"
    return text

async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_daily(), reply_markup=main_kb())

async def monthly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_monthly(), reply_markup=main_kb())

async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", ('report_chat_id', str(chat_id)))
    conn.commit(); conn.close()
    for job in context.job_queue.get_jobs_by_name('auto_daily'):
        job.schedule_removal()
    context.job_queue.run_daily(
        auto_daily_job,
        time=datetime.time(hour=15, minute=0, tzinfo=datetime.timezone.utc),
        data=chat_id, name='auto_daily'
    )
    await update.message.reply_text("Avtomatik hisobot sozlandi!\n\nHar kuni soat 20:00 da (Toshkent vaqti) hisobot yuboriladi.", reply_markup=main_kb())

async def auto_daily_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.data, text="AVTOMATIK KUNLIK HISOBOT\n\n" + build_daily())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bekor qilindi.", reply_markup=main_kb())
    return ConversationHandler.END

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    if txt == "Qoldiqlar": await stock_command(update, context)
    elif txt == "Tarix": await history_command(update, context)
    elif txt == "Kunlik hisobot": await daily_report(update, context)
    elif txt == "Oylik hisobot": await monthly_report(update, context)
    elif txt == "Sozlamalar": await setup_command(update, context)

def main():
    init_db()
    TOKEN = os.environ.get('BOT_TOKEN')
    if not TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi!")

    app = Application.builder().token(TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler('add_item', add_item_start), MessageHandler(filters.Regex("^Tovar qoshish$"), add_item_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_item_name)],
            ADD_UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_item_unit)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    in_conv = ConversationHandler(
        entry_points=[CommandHandler('incoming', incoming_start), MessageHandler(filters.Regex("^Kirim$"), incoming_start)],
        states={
            IN_SELECT: [CallbackQueryHandler(incoming_select, pattern=r'^(in_\d+|cancel)$')],
            IN_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, incoming_qty)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    out_conv = ConversationHandler(
        entry_points=[CommandHandler('outgoing', outgoing_start), MessageHandler(filters.Regex("^Chiqim$"), outgoing_start)],
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
    app.add_handler(MessageHandler(filters.Regex("^(Qoldiqlar|Tarix|Kunlik hisobot|Oylik hisobot|Sozlamalar)$"), handle_buttons))

    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='report_chat_id'").fetchone()
    conn.close()
    if row:
        app.job_queue.run_daily(
            auto_daily_job,
            time=datetime.time(hour=15, minute=0, tzinfo=datetime.timezone.utc),
            data=int(row['value']), name='auto_daily'
        )

    logger.info("Bot ishga tushdi!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
