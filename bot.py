import asyncio
import random
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# Вставь токен от @BotFather между кавычками.
TOKEN = "8887088522:AAG_kB9U4U-8tPDd9x1u1BAhmF-jeTzom5U"
DB = "casino.db"

if TOKEN == "ВСТАВЬ_СЮДА_ТОКЕН":
    raise RuntimeError("Вставь токен от @BotFather в переменную TOKEN")

bot = Bot(TOKEN)
dp = Dispatcher()

# Ожидания ввода игрока и текущие партии хранятся в памяти.
pending = {}  # uid -> {"type": "bet" | "roulette_number", "game": ...}
games = {}    # uid -> данные партии

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def connect():
    con = sqlite3.connect(DB, timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS users(
        uid INTEGER PRIMARY KEY,
        name TEXT,
        balance INTEGER NOT NULL DEFAULT 20,
        blocked INTEGER NOT NULL DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value INTEGER NOT NULL
    )""")
    defaults = {
        "starting_balance": 20,
        "min_bet": 1,
        "max_bet": 1000,
        "roulette_red_payout": 2,
        "roulette_black_payout": 2,
        "roulette_zero_payout": 36,
        "roulette_number_payout": 36,
        "slot_three_payout": 10,
        "slot_pair_payout": 2,
        "blackjack_payout": 2,
    }
    for key, value in defaults.items():
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
    # Вес каждого числа: 1 означает стандартный одинаковый вес.
    for n in range(37):
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                    (f"roulette_weight_{n}", 1))
    con.commit()
    return con


def get_setting(key):
    con = connect()
    try:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else 1
    finally:
        con.close()


def set_setting(key, value):
    con = connect()
    try:
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )
        con.commit()
    finally:
        con.close()


def ensure_user(uid, name):
    con = connect()
    try:
        con.execute(
            "INSERT OR IGNORE INTO users(uid,name,balance) VALUES(?,?,?)",
            (uid, name, get_setting("starting_balance"))
        )
        con.execute("UPDATE users SET name=? WHERE uid=?", (name, uid))
        row = con.execute(
            "SELECT balance,blocked FROM users WHERE uid=?", (uid,)
        ).fetchone()
        con.commit()
        return row
    finally:
        con.close()


def change_balance(uid, delta):
    con = connect()
    try:
        con.execute("UPDATE users SET balance=balance+? WHERE uid=?", (delta, uid))
        con.commit()
    finally:
        con.close()


def balance_of(uid):
    con = connect()
    try:
        row = con.execute("SELECT balance FROM users WHERE uid=?", (uid,)).fetchone()
        return row[0] if row else 0
    finally:
        con.close()


def is_blocked(uid, name):
    return bool(ensure_user(uid, name)[1])


def menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="🎡 Рулетка")],
            [KeyboardButton(text="🃏 Блэкджек"), KeyboardButton(text="🎰 Автоматы")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True
    )


def bet_keyboard(game):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Своя ставка", callback_data=f"custom:{game}")],
        [InlineKeyboardButton(text="10 F$", callback_data=f"bet:{game}:10"),
         InlineKeyboardButton(text="20 F$", callback_data=f"bet:{game}:20"),
         InlineKeyboardButton(text="50 F$", callback_data=f"bet:{game}:50")],
        [InlineKeyboardButton(text="100 F$", callback_data=f"bet:{game}:100")]
    ])


def roulette_choice_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Красное", callback_data="r:color:red"),
         InlineKeyboardButton(text="⚫ Чёрное", callback_data="r:color:black")],
        [InlineKeyboardButton(text="🟢 Зеро (0)", callback_data="r:color:zero")],
        [InlineKeyboardButton(text="🔢 Выбрать число 0–36", callback_data="r:number")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])


def bj_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Взять карту", callback_data="bj:hit"),
        InlineKeyboardButton(text="✋ Стоп", callback_data="bj:stand")
    ]])


def draw_card():
    return random.randint(1, 13)


def card_value(card):
    return 11 if card == 1 else min(card, 10)


def hand_value(hand):
    total = sum(card_value(c) for c in hand)
    aces = hand.count(1)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def hand_text(hand):
    names = {1: "A", 11: "J", 12: "Q", 13: "K"}
    return " ".join(names.get(c, str(c)) for c in hand)


def roulette_color(number):
    if number == 0:
        return "zero"
    return "red" if number in RED_NUMBERS else "black"


def spin_roulette():
    numbers = list(range(37))
    weights = [max(0, get_setting(f"roulette_weight_{n}")) for n in numbers]
    if sum(weights) <= 0:
        weights = [1] * 37
    return random.choices(numbers, weights=weights, k=1)[0]


def begin_bet(uid, game, bet):
    minimum, maximum = get_setting("min_bet"), get_setting("max_bet")
    if not minimum <= bet <= maximum:
        return f"Допустимая ставка: {minimum}–{maximum} F$."
    if balance_of(uid) < bet:
        return "Недостаточно F$ на балансе."
    if uid in games:
        return "Сначала закончи текущую игру."
    change_balance(uid, -bet)
    games[uid] = {"game": game, "bet": bet}
    return None


@dp.message(CommandStart())
async def start(m: Message):
    bal, blocked = ensure_user(m.from_user.id, m.from_user.full_name)
    if blocked:
        return await m.answer("⛔ Доступ заблокирован.")
    await m.answer(
        f"🎰 Добро пожаловать в виртуальное казино!\n"
        f"Твой баланс: {bal} F$.\n\n"
        "F$ —  игровые фишки",
        reply_markup=menu()
    )


@dp.message(F.text == "💰 Баланс")
async def balance_message(m: Message):
    bal, blocked = ensure_user(m.from_user.id, m.from_user.full_name)
    if blocked:
        return await m.answer("⛔ Доступ заблокирован.")
    await m.answer(f"💰 Баланс: {bal} F$.", reply_markup=menu())


@dp.message(F.text == "🎡 Рулетка")
async def roulette(m: Message):
    if is_blocked(m.from_user.id, m.from_user.full_name):
        return await m.answer("⛔ Доступ заблокирован.")
    await m.answer("🎡 РУЛЕТКА\nВыбери ставку:", reply_markup=bet_keyboard("roulette"))


@dp.message(F.text == "🃏 Блэкджек")
async def blackjack(m: Message):
    if is_blocked(m.from_user.id, m.from_user.full_name):
        return await m.answer("⛔ Доступ заблокирован.")
    await m.answer("🃏 БЛЭКДЖЕК\nВыбери ставку:", reply_markup=bet_keyboard("bj"))


@dp.message(F.text == "🎰 Автоматы")
async def slots(m: Message):
    if is_blocked(m.from_user.id, m.from_user.full_name):
        return await m.answer("⛔ Доступ заблокирован.")
    await m.answer("🎰 АВТОМАТЫ\nВыбери ставку:", reply_markup=bet_keyboard("slots"))


@dp.message(F.text == "📊 Статистика")
async def stats(m: Message):
    bal, blocked = ensure_user(m.from_user.id, m.from_user.full_name)
    if blocked:
        return await m.answer("⛔ Доступ заблокирован.")
    con = connect()
    try:
        count = con.execute("SELECT COUNT(*) FROM users WHERE blocked=0").fetchone()[0]
    finally:
        con.close()
    await m.answer(f"📊 Активных игроков: {count}\nТвой баланс: {bal} F$.", reply_markup=menu())


@dp.message(F.text == "ℹ️ Помощь")
async def help_message(m: Message):
    await m.answer(
        "ℹ️ Все F$ — игровая валюта.\n"
        "Выбери игру, ставку и следуй подсказкам.\n"
        "Рулетка: красное, чёрное, зеро или конкретное число 0–36.\n"
        "Своя ставка доступна через кнопку «Своя ставка».",
        reply_markup=menu()
    )


@dp.callback_query(F.data.startswith("custom:"))
async def custom_bet(q: CallbackQuery):
    uid = q.from_user.id
    if is_blocked(uid, q.from_user.full_name):
        return await q.answer("Доступ заблокирован.", show_alert=True)
    game = q.data.split(":", 1)[1]
    pending[uid] = {"type": "bet", "game": game}
    await q.answer()
    await q.message.answer(
        f"Введи ставку целым числом от {get_setting('min_bet')} до "
        f"{get_setting('max_bet')} F$.\nДля отмены напиши «отмена»."
    )


@dp.callback_query(F.data.startswith("bet:"))
async def preset_bet(q: CallbackQuery):
    try:
        _, game, raw = q.data.split(":")
        bet = int(raw)
    except ValueError:
        return await q.answer("Некорректная ставка.", show_alert=True)
    uid = q.from_user.id
    if is_blocked(uid, q.from_user.full_name):
        return await q.answer("Доступ заблокирован.", show_alert=True)
    error = begin_bet(uid, game, bet)
    if error:
        return await q.answer(error, show_alert=True)
    await q.answer()
    await launch_game(q.message, uid, game, bet)


@dp.message(F.text)
async def text_input(m: Message):
    uid = m.from_user.id
    if m.text.lower().strip() == "отмена":
        pending.pop(uid, None)
        return await m.answer("Ввод отменён.", reply_markup=menu())
    if uid not in pending:
        return
    if is_blocked(uid, m.from_user.full_name):
        pending.pop(uid, None)
        return await m.answer("⛔ Доступ заблокирован.")
    state = pending[uid]
    if state["type"] == "bet":
        try:
            bet = int(m.text.strip())
        except ValueError:
            return await m.answer("Введи ставку целым числом, например 25.")
        game = state["game"]
        error = begin_bet(uid, game, bet)
        if error:
            return await m.answer(error)
        pending.pop(uid, None)
        await launch_game(m, uid, game, bet)
    elif state["type"] == "roulette_number":
        try:
            number = int(m.text.strip())
        except ValueError:
            return await m.answer("Введи целое число от 0 до 36.")
        if not 0 <= number <= 36:
            return await m.answer("Число должно быть от 0 до 36.")
        pending.pop(uid, None)
        await settle_roulette(m, uid, "number", number)


async def launch_game(message, uid, game, bet):
    if game == "roulette":
        await message.answer(
            f"Ставка принята: {bet} F$.\nНа что ставишь?",
            reply_markup=roulette_choice_keyboard()
        )
    elif game == "slots":
        symbols = ["🍒", "🍋", "⭐", "🔔", "💎"]
        reels = [random.choice(symbols) for _ in range(3)]
        multiplier = (get_setting("slot_three_payout") if len(set(reels)) == 1
                      else get_setting("slot_pair_payout") if len(set(reels)) == 2
                      else 0)
        payout = bet * multiplier
        change_balance(uid, payout)
        games.pop(uid, None)
        await message.answer(
            f"🎰 {' | '.join(reels)}\n"
            f"{'🎉 Выигрыш!' if payout else 'В этот раз без выигрыша.'}\n"
            f"Выплата: {payout} F$\nБаланс: {balance_of(uid)} F$",
            reply_markup=menu()
        )
    elif game == "bj":
        player = [draw_card(), draw_card()]
        dealer = [draw_card(), draw_card()]
        games[uid].update({"player": player, "dealer": dealer})
        if hand_value(player) == 21:
            payout = int(bet * 2.5) if hand_value(dealer) != 21 else bet
            change_balance(uid, payout)
            games.pop(uid, None)
            await message.answer(
                f"🃏 Твои карты: {hand_text(player)} = 21\n"
                f"Дилер: {hand_text(dealer)} = {hand_value(dealer)}\n"
                f"{'Ничья!' if payout == bet else 'Блэкджек!'} Выплата: {payout} F$.\n"
                f"Баланс: {balance_of(uid)} F$",
                reply_markup=menu()
            )
        else:
            await message.answer(
                f"🃏 Твои карты: {hand_text(player)} = {hand_value(player)}\n"
                f"Дилер: {hand_text(dealer[:1])} + ❓\nТвой ход:",
                reply_markup=bj_keyboard()
            )


@dp.callback_query(F.data.startswith("r:"))
async def roulette_action(q: CallbackQuery):
    uid = q.from_user.id
    game = games.get(uid)
    if not game or game.get("game") != "roulette":
        return await q.answer("Нет активной ставки на рулетку.", show_alert=True)
    parts = q.data.split(":")
    if parts[1] == "number":
        pending[uid] = {"type": "roulette_number"}
        await q.answer()
        return await q.message.answer("Введи число от 0 до 36:")
    if len(parts) != 3 or parts[2] not in ("red", "black", "zero"):
        return await q.answer("Выбор не распознан.", show_alert=True)
    await q.answer()
    await settle_roulette(q.message, uid, "color", parts[2])


async def settle_roulette(message, uid, choice_type, choice):
    game = games.get(uid)
    if not game or game.get("game") != "roulette":
        return await message.answer("Ставка на рулетку уже завершена.")
    bet = game["bet"]
    number = spin_roulette()
    color = roulette_color(number)
    if choice_type == "number":
        won = number == choice
        multiplier = get_setting("roulette_number_payout")
        label = f"число {choice}"
    else:
        won = color == choice
        multiplier = {
            "red": get_setting("roulette_red_payout"),
            "black": get_setting("roulette_black_payout"),
            "zero": get_setting("roulette_zero_payout"),
        }[choice]
        label = {"red": "красное", "black": "чёрное", "zero": "зеро"}[choice]
    payout = bet * multiplier if won else 0
    if payout:
        change_balance(uid, payout)
    games.pop(uid, None)
    pending.pop(uid, None)
    color_label = {"red": "🔴 красное", "black": "⚫ чёрное", "zero": "🟢 зеро"}[color]
    await message.answer(
        f"🎡 Выпало: {number} ({color_label}).\n"
        f"Твоя ставка: {label} — {bet} F$.\n"
        f"{'🎉 Победа!' if won else 'Не выиграл в этот раз.'}\n"
        f"Выплата: {payout} F$\nБаланс: {balance_of(uid)} F$",
        reply_markup=menu()
    )


@dp.callback_query(F.data == "cancel")
async def cancel(q: CallbackQuery):
    uid = q.from_user.id
    game = games.pop(uid, None)
    pending.pop(uid, None)
    if game:
        change_balance(uid, game["bet"])
    await q.answer("Отменено.")
    await q.message.answer("Игра отменена, ставка возвращена.", reply_markup=menu())


@dp.callback_query(F.data.startswith("bj:"))
async def blackjack_turn(q: CallbackQuery):
    uid = q.from_user.id
    g = games.get(uid)
    if not g or g.get("game") != "bj":
        return await q.answer("Игра уже завершена.", show_alert=True)

    if q.data == "bj:hit":
        g["player"].append(draw_card())
        pv = hand_value(g["player"])
        if pv < 21:
            await q.answer()
            return await q.message.edit_text(
                f"🃏 Твои карты: {hand_text(g['player'])} = {pv}\n"
                f"Дилер: {hand_text(g['dealer'][:1])} + ❓\nТвой ход:",
                reply_markup=bj_keyboard()
            )
        if pv > 21:
            games.pop(uid, None)
            await q.answer()
            return await q.message.edit_text(
                f"💥 Перебор: {hand_text(g['player'])} = {pv}.\n"
                f"Ставка потеряна. Баланс: {balance_of(uid)} F$."
            )

    await q.answer()
    while hand_value(g["dealer"]) < 17:
        g["dealer"].append(draw_card())
    pv, dv = hand_value(g["player"]), hand_value(g["dealer"])
    payout = g["bet"] * 2 if pv <= 21 and (dv > 21 or pv > dv) else (
        g["bet"] if pv == dv else 0
    )
    change_balance(uid, payout)
    games.pop(uid, None)
    await q.message.edit_text(
        f"🃏 Ты: {hand_text(g['player'])} = {pv}\n"
        f"Дилер: {hand_text(g['dealer'])} = {dv}\n"
        f"{'🏆 Победа!' if payout > g['bet'] else '🤝 Ничья!' if payout else 'Дилер победил.'}\n"
        f"Выплата: {payout} F$\nБаланс: {balance_of(uid)} F$"
    )


async def main():
    con = connect()
    con.close()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
