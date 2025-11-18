# bot3_0.py
# Python 3.10+
# pip install python-telegram-bot==20.7

import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
print("MY_BOT_TOKEN из окружения:", os.environ.get("MY_BOT_TOKEN"))  # <-- временный вывод

TOKEN = os.environ.get("MY_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Переменная окружения MY_BOT_TOKEN не задана")
ADMIN_CHAT_ID = 824058186  # твой чат-id

# ----- States -----
(
    ASK_NAME,
    ASK_TY,
    ASK_DATE,
    ASK_EVENT,
    ASK_TARIFF,
    ASK_TIME_RANGE,        # почасовые
    ASK_GUESTS,            # и для Божества сначала
    ASK_START_TIME_BOZH,   # Божество
    ASK_COCKTAILS_BOZH,    # Божество
    ASK_GEO,
    ASK_ADDRESS_OR_HINT,
    ASK_PHONE,
    CONFIRM_SUMMARY,
    EDIT_MENU,
    EDIT_VALUE,
) = range(15)

# ----- Ставки -----
TARIFFS = ["Монах Капуцин", "Пастор", "Святой", "Божество"]

RATES_BASE = {
    "Монах Капуцин": {10: 1400, 15: 1500, 20: 1600, 25: 1700, 30: 1800},
    "Пастор":        {10: 2000, 15: 2100, 20: 2200, 25: 2300, 30: 2400},
    "Святой":        {10: 2500, 15: 2600, 20: 2700, 25: 2800, 30: 2900},
}
EXTEND_PER_HOUR = {"Монах Капуцин": 600, "Пастор": 1000, "Святой": 1500}
MOS_OBLAST_SURCHARGE_PER_HOUR = 700  # только для почасовых

# Божество — фикс-пакеты (без надбавок области, без такси, без продления)
BOZH_TIERS = {100: 70000, 150: 102000, 200: 132000, 300: 192000}
BOZH_AFTER_300_PRICE_PER_COCKTAIL = 640
BOZH_INCLUDED_HOURS = 6  # инфо в описании

# ----- Хелперы «ты/вы» -----
def informal(u): return bool(u.get("informal"))
def you(u):   return "ты" if informal(u) else "Вы"
def your(u):  return "твой" if informal(u) else "Ваш"
def you_obj(u): return "тебя" if informal(u) else "Вас"

# ----- Валидация -----
def valid_date(s):
    try:
        datetime.strptime(s, "%d.%m.%Y")
        return True
    except ValueError:
        return False

def valid_time(s):
    return re.fullmatch(r"(?:[01]\d|2[0-3]):(?:00|30)", s) is not None

def valid_time_range(s, min_hours=4):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):(?:00|30)-(?:[01]\d|2[0-3]):(?:00|30)", s):
        return False
    start, end = s.split("-")
    st = datetime.strptime(start, "%H:%M")
    en = datetime.strptime(end, "%H:%M")
    if en <= st:
        en += timedelta(days=1)
    diff = en - st
    return diff >= timedelta(hours=min_hours) and (diff.total_seconds() / 1800).is_integer()

def parse_hours(time_range: str) -> float:
    start, end = time_range.split("-")
    st = datetime.strptime(start, "%H:%M")
    en = datetime.strptime(end, "%H:%M")
    if en <= st:
        en += timedelta(days=1)
    return (en - st).total_seconds() / 3600.0

def valid_phone(s): return re.fullmatch(r"\d{11}", s) is not None

# ----- Расчёты -----
def hourly_rate_for_guests(tariff: str, guests: int) -> int:
    base = RATES_BASE[tariff]
    if guests <= 10: return base[10]
    if guests <= 15: return base[15]
    if guests <= 20: return base[20]
    if guests <= 25: return base[25]
    if guests <= 30: return base[30]
    over = max(0, guests - 30)
    steps = (over + 4) // 5
    return base[30] + steps * 200  # каждые 5 гостей +200/ч

def price_hourly_package(tariff: str, guests: int, hours: float, region: str) -> int:
    rate = hourly_rate_for_guests(tariff, guests)
    subtotal = int(rate * hours)
    if region == "Мос область":
        subtotal += int(MOS_OBLAST_SURCHARGE_PER_HOUR * hours)  # только почасовые
    return subtotal

def price_bozhestvo(cocktails: int) -> int:
    if cocktails in BOZH_TIERS:
        return BOZH_TIERS[cocktails]
    if cocktails > 300:
        return BOZH_TIERS[300] + (cocktails - 300) * BOZH_AFTER_300_PRICE_PER_COCKTAIL
    return max(v for k, v in BOZH_TIERS.items() if k <= cocktails)

def bozh_bonus_text(cocktails: int) -> str:
    if cocktails <= 100: bonus = 20
    elif cocktails <= 150: bonus = 25
    elif cocktails <= 200: bonus = 30
    elif cocktails <= 300: bonus = 40
    else: bonus = 40 + ((cocktails - 300) // 50) * 5
    return f"+{bonus} авторских в подарок"

# ----- Тарифы (описание — дословно как просил) -----
def tariffs_text(u) -> str:
    lines = []
    lines.append("📦 Тарифные планы HOLY MIXOLOG")
    lines.append("")
    lines.append("Монах Капуцин - Этот пакет включает только выезд и работу бармена в день мероприятия. Идеальный выбор, если всё уже организовано, и нужен просто профи за стойкой")
    lines.append("Пастор - Помимо работы бармена, вы получите помощь в составлении барного меню, а также полную смету: где, что и в каком объёме покупать - с конкретными брендами и растчётами под ваше мероприятие")
    lines.append("Святой - идеально для тех, кто хочет не просто бар, а запоминающийся вечер. Пакет включает то же, что \"Пастор\", плюс: Авторский именной коктейль, созданный под ваш вкус и предпочтения возможность подачи шотов — как классических, так и фирменных декоративное оформление напитков гибкость: бармен может выходить за рамки меню и импровизировать прямо на месте")
    lines.append("Божество - Абсолютный комфорт и максимум заботы со стороны HOLY MIXOLOG. Полный бар под ключ. Вам нужно лишь выбрать количество коктейлей и согласовать меню — всё остальное сделаем мы. самостоятельно закупим все ингредиенты всё привезём и подготовим Включено 6 часов работы (или пока не закончатся коктейли) бар будет полностью на нашей ответственности — от начала и до конца")
    lines.append("⚠️ Важно: барная стойка в стоимость не входит")
    lines.append("")
    lines.append("💰 Стоимость (за час, в зависимости от количества гостей):")
    lines.append("до 10 чел — Монах 1400 ₽/ч · Пастор 2000 ₽/ч · Святой 2500 ₽/ч")
    lines.append("до 15 чел — Монах 1500 ₽/ч · Пастор 2100 ₽/ч · Святой 2600 ₽/ч")
    lines.append("до 20 чел — Монах 1600 ₽/ч · Пастор 2200 ₽/ч · Святой 2700 ₽/ч")
    lines.append("до 25 чел — Монах 1700 ₽/ч · Пастор 2300 ₽/ч · Святой 2800 ₽/ч")
    lines.append("до 30 чел — Монах 1800 ₽/ч · Пастор 2400 ₽/ч · Святой 2900 ₽/ч")
    lines.append("Свыше 30 человек — каждые 5 гостей +200 ₽/час")
    lines.append("Продление: Монах +600 ₽/ч · Пастор +1000 ₽/ч · Святой +1500 ₽/ч")
    lines.append("Если мероприятие проходит в Московской области — к любой ставке добавляется +700 ₽/час")
    lines.append("")
    lines.append("👑 Божество (бар под ключ):")
    lines.append("100 коктейлей (+20 авторских в подарок) — от 70000р")
    lines.append("150 коктейлей (+25 авторских в подарок) — от 102000р")
    lines.append("200 коктейлей (+30 авторских в подарок) — от 132000р")
    lines.append("300 коктейлей (+40 авторских в подарок) — от 192000р")
    lines.append("После 300 коктейлей — шаг 50 коктейлей (+5 авторских), цена остаётся 640 ₽ за коктейль")
    return "\n".join(lines)

# Такси — только для почасовых
def taxi_warning_text() -> str:
    return ("Учти: если метро закрывается - работаем с условием такси до дома. "
            "Если работа в области, метро работает, а автобусы или электрички не ходят - такси до метро")

def taxi_warning_text_formal() -> str:
    return ("Учтите: если метро закрывается - работаем с условием такси до дома. "
            "Если работа в области, метро работает, а автобусы или электрички не ходят - такси до метро")

# Благодарность после подтверждения
def thank_you_text(u):
    return ("Спасибо! Миксолог скоро свяжется 📲"
            if informal(u) else
            "Спасибо! Миксолог скоро с вами свяжется 📲")

# ----- Сценарий -----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Привет! 👋 Давайте начнём оформление заявки. Как вас зовут?")
    return ASK_NAME

async def ask_ty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Да 🙌", callback_data="ty_yes"),
                                InlineKeyboardButton("Нет 🙏", callback_data="ty_no")]])
    await update.message.reply_text(f"{context.user_data['name']}, можем перейти на «ты»?", reply_markup=kb)
    return ASK_TY

async def set_ty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["informal"] = (q.data == "ty_yes")
    if informal(context.user_data):
        await q.edit_message_text("Отлично, на «ты» 😎")
        await q.message.reply_text("Укажи дату мероприятия (формат 15.06.2026) 📅")
    else:
        await q.edit_message_text("Хорошо, будем на «Вы» 🙂")
        await q.message.reply_text("Укажите дату мероприятия (формат 15.06.2026) 📅")
    return ASK_DATE

async def ask_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date = update.message.text.strip()
    if not valid_date(date):
        await update.message.reply_text("Формат: ДД.ММ.ГГГГ 🙂")
        return ASK_DATE
    context.user_data["date"] = date

    if informal(context.user_data):
        await update.message.reply_text("Что будем отмечать? (день рождения, свадьба, корпоратив и т.д.) 🎉")
    else:
        await update.message.reply_text("Что будет отмечать? (день рождения, свадьба, корпоратив и т.д.) 🎉")
    return ASK_EVENT

async def ask_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["event_type"] = update.message.text.strip()

    await update.message.reply_text(tariffs_text(context.user_data))

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🥤 Монах Капуцин", callback_data="t_Монах Капуцин")],
        [InlineKeyboardButton("💼 Пастор", callback_data="t_Пастор")],
        [InlineKeyboardButton("✨ Святой", callback_data="t_Святой")],
        [InlineKeyboardButton("👑 Божество", callback_data="t_Божество")],
    ])
    await update.message.reply_text("Выбери пакет услуг ⬇️" if informal(context.user_data) else "Выберите пакет услуг ⬇️",
                                    reply_markup=kb)
    return ASK_TARIFF

async def choose_tariff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tariff = q.data.split("_", 1)[1]
    context.user_data["tariff"] = tariff
    title = "Ты выбрал: " if informal(context.user_data) else "Вы выбрали: "
    await q.edit_message_text(f"{title}*{tariff}*", parse_mode="Markdown")

    if tariff in ("Монах Капуцин", "Пастор", "Святой"):
        if informal(context.user_data):
            await q.message.reply_text(
                "Со скольки и до скольки тебе потребуется услуга? (мин 4 часа, формат:15:00-21:00. шаг 30 минут) ⏰"
            )
            await q.message.reply_text(taxi_warning_text())
        else:
            await q.message.reply_text(
                "Со скольки и до скольки вам потребуется услуга? (мин 4 часа, формат:15:00-21:00. шаг 30 минут) ⏰"
            )
            await q.message.reply_text(taxi_warning_text_formal())
        return ASK_TIME_RANGE
    else:
        await q.message.reply_text("Сколько будет человек на мероприятии? 🙂")
        return ASK_GUESTS

async def time_range(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tr = update.message.text.strip()
    if not valid_time_range(tr, 4):
        await update.message.reply_text("Нужно вот так: 15:00-21:00 (шаг 30 минут, минимум 4 часа).")
        return ASK_TIME_RANGE
    context.user_data["time_range"] = tr
    if informal(context.user_data):
        await update.message.reply_text("Сколько у тебя будет гостей? 👥")
    else:
        await update.message.reply_text("Сколько у вас будет гостей? 👥")
    return ASK_GUESTS

async def guests_then_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text("Напиши числом, пожалуйста 🙂" if informal(context.user_data) else "Напишите числом, пожалуйста 🙂")
        return ASK_GUESTS
    guests = int(txt)
    context.user_data["guests"] = guests

    if context.user_data.get("tariff") == "Божество":
        if informal(context.user_data):
            await update.message.reply_text("Напиши время начала работы (формат 15:00, шаг 30 минут) ⏰")
        else:
            await update.message.reply_text("Напишите время начала работы (формат 15:00, шаг 30 минут) ⏰")
        return ASK_START_TIME_BOZH
    else:
        return await ask_geo_step(update, context)

async def start_time_bozh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if not valid_time(t):
        await update.message.reply_text("Формат времени: ЧЧ:ММ (шаг 30 минут). Например, 15:00")
        return ASK_START_TIME_BOZH
    context.user_data["start_time_bozh"] = t
    if informal(context.user_data):
        await update.message.reply_text("Сколько коктейлей планируешь? (минимум 100, шаг 50) 🍹")
    else:
        await update.message.reply_text("Сколько коктейлей планируете? (минимум 100, шаг 50) 🍹")
    return ASK_COCKTAILS_BOZH

async def cocktails_bozh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) < 100 or int(txt) % 50 != 0:
        await update.message.reply_text("Минимум 100 и шаг 50. Например: 150, 200, 250, 300, 350.")
        return ASK_COCKTAILS_BOZH
    context.user_data["cocktails"] = int(txt)
    return await ask_geo_step(update, context)

async def ask_geo_step(update_or_message, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Москва", callback_data="geo_Москва")],
        [InlineKeyboardButton("Мос область", callback_data="geo_Мос область")],
        [InlineKeyboardButton("Другой город", callback_data="geo_Другой город")],
    ])
    msg = update_or_message.message if isinstance(update_or_message, Update) else update_or_message
    await msg.reply_text("Где будет проходить мероприятие? 📍", reply_markup=kb)
    return ASK_GEO

async def choose_geo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    region = q.data.split("_", 1)[1]
    context.user_data["region"] = region
    await q.edit_message_text(f"Локация: *{region}*", parse_mode="Markdown")

    if region in ("Москва", "Мос область"):
        await q.message.reply_text("Напиши адрес места проведения 🗺️" if informal(context.user_data)
                                   else "Напишите адрес места проведения 🗺️")
        return ASK_ADDRESS_OR_HINT
    else:
        await q.message.reply_text("Тогда лучше напрямую написать нашему миксологу для расчёта логистики — @smamedliiii ✈️")
        return await ask_phone_step(q.message, context)

async def address_or_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["address"] = update.message.text.strip()
    return await ask_phone_step(update.message, context)

async def ask_phone_step(message, context: ContextTypes.DEFAULT_TYPE):
    if informal(context.user_data):
        await message.reply_text("Оставь номер телефона (только цифры, формат: 89999256074) 📞")
    else:
        await message.reply_text("Оставьте номер телефона (только цифры, формат: 89999256074) 📞")
    return ASK_PHONE

async def phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ph = update.message.text.strip()
    if not valid_phone(ph):
        await update.message.reply_text("Нужны только цифры, 11 знаков. Например: 89991234567")
        return ASK_PHONE
    context.user_data["phone"] = ph
    return await show_summary(update, context)

# ----- Итог -----
def calc_price_text(user_data) -> tuple[int | None, str]:
    tariff = user_data.get("tariff")
    region = user_data.get("region")

    if tariff in ("Монах Капуцин", "Пастор", "Святой"):
        hours = parse_hours(user_data["time_range"])
        guests = user_data["guests"]
        price = price_hourly_package(tariff, guests, hours, region)
        breakdown = (
            f"{tariff}: {hourly_rate_for_guests(tariff, guests)} ₽/ч × {hours:.1f} ч"
            + (f" + область {MOS_OBLAST_SURCHARGE_PER_HOUR} ₽/ч × {hours:.1f} ч" if region == "Мос область" else "")
            + f" = *{price:,} ₽*".replace(",", " ")
        )
        return price, breakdown

    if tariff == "Божество":
        cocktails = user_data["cocktails"]
        price = price_bozhestvo(cocktails)  # без надбавок/такси/продл.
        breakdown = f"Божество: {cocktails} кокт. ({bozh_bonus_text(cocktails)}) = *{price:,} ₽*".replace(",", " ")
        return price, breakdown

    return None, "Стоимость будет рассчитана индивидуально."

def summary_text(user_data) -> str:
    price, breakdown = calc_price_text(user_data)
    lines = [
        "🧾 *Проверьте детали заявки:*",
        f"Имя: {user_data.get('name')}",
        f"Дата: {user_data.get('date')}",
        f"Тип события: {user_data.get('event_type')}",
        f"Тариф: {user_data.get('tariff')}",
    ]
    if user_data.get("tariff") == "Божество":
        lines += [
            f"Гостей: {user_data.get('guests')}",
            f"Начало работы: {user_data.get('start_time_bozh')}",
            f"Коктейлей: {user_data.get('cocktails')} ({bozh_bonus_text(user_data.get('cocktails'))})",
        ]
    else:
        lines += [
            f"Время работы: {user_data.get('time_range')}",
            f"Гостей: {user_data.get('guests')}",
        ]
    lines += [f"Геолокация: {user_data.get('region')}"]
    if user_data.get("address"):
        lines.append(f"Адрес: {user_data.get('address')}")
    lines.append(f"Телефон: {user_data.get('phone')}")
    lines.append("")
    lines.append(f"Итог: {breakdown}")
    return "\n".join(lines)

async def show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = summary_text(context.user_data)
    await update.message.reply_text(text, parse_mode="Markdown")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Всё правильно", callback_data="ok")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data="edit")]
    ])
    ask = "Всё ли указано верно или хочешь что-то изменить? 🙂" if informal(context.user_data)\
          else "Всё ли указано верно или хотите что-то изменить? 🙂"
    await update.message.reply_text(ask, reply_markup=kb)
    return CONFIRM_SUMMARY

# ----- Редактирование -----
def edit_keyboard(u):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣ Имя", callback_data="e_name"),
         InlineKeyboardButton("2️⃣ Дата", callback_data="e_date")],
        [InlineKeyboardButton("3️⃣ Тип события", callback_data="e_event"),
         InlineKeyboardButton("4️⃣ Тариф", callback_data="e_tariff")],
        [InlineKeyboardButton("5️⃣ Кол-во человек", callback_data="e_guests"),
         InlineKeyboardButton("6️⃣ Время работы", callback_data="e_time")],
        [InlineKeyboardButton("7️⃣ Кол-во коктейлей", callback_data="e_cocktails"),
         InlineKeyboardButton("8️⃣ Геолокация", callback_data="e_geo")],
        [InlineKeyboardButton("9️⃣ Телефон", callback_data="e_phone")],
        [InlineKeyboardButton("🔟 Всё верно", callback_data="e_done"),
         InlineKeyboardButton("1️⃣1️⃣ Отмена", callback_data="e_cancel")],
    ])

async def confirm_or_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "ok":
        await send_application_to_admin(context, q.from_user, context.user_data)
        await q.edit_message_text(thank_you_text(context.user_data))
        return ConversationHandler.END
    else:
        await q.edit_message_text("Что хотите отредактировать?" if not informal(context.user_data) else "Что хочешь отредактировать?")
        await q.message.reply_text("Выберите пункт:" if not informal(context.user_data) else "Выбери пункт:", reply_markup=edit_keyboard(context.user_data))
        return EDIT_MENU

async def edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data

    if key == "e_done":
        await send_application_to_admin(context, q.from_user, context.user_data)
        await q.message.reply_text(thank_you_text(context.user_data))
        return ConversationHandler.END

    if key == "e_cancel":
        now = datetime.now(ZoneInfo("Europe/Moscow"))
        hour = now.hour
        if 4 <= hour < 17:
            text = "Жаль, что не сможем поработать вместе, но я желаю тебе хорошего дня 🙂" if informal(context.user_data) \
                   else "Жаль, что не сможем поработать вместе, но я желаю вам хорошего дня 🙂"
        elif 17 <= hour < 24:
            text = "Жаль, что не сможем поработать вместе, но я желаю тебе хорошего вечера 🌇" if informal(context.user_data) \
                   else "Жаль, что не сможем поработать вместе, но я желаю вам хорошего вечера 🌆"
        else:
            text = "Жаль, что не сможем поработать вместе, но я желаю тебе доброй ночи 🌙" if informal(context.user_data) \
                   else "Жаль, что не сможем поработать вместе, но я желаю вам доброй ночи 🌙"
        await q.message.reply_text(text)
        return ConversationHandler.END

    # пункты 1–9
    context.user_data["edit_key"] = key
    prompts = {
        "e_name": "Введи новое имя 🙂" if informal(context.user_data) else "Введите новое имя 🙂",
        "e_date": "Введи дату (ДД.ММ.ГГГГ) 📅" if informal(context.user_data) else "Введите дату (ДД.ММ.ГГГГ) 📅",
        "e_event": "Что будем отмечать? 🎉" if informal(context.user_data) else "Что будет отмечать? 🎉",
        "e_tariff": "Выбери тариф снова:" if informal(context.user_data) else "Выберите тариф снова:",
        "e_guests": "Сколько у тебя будет гостей? 👥" if informal(context.user_data) else "Сколько у вас будет гостей? 👥",
        "e_time": "Укажи время (15:00-21:00, шаг 30 минут, минимум 4 часа) ⏰" if informal(context.user_data) else "Укажите время (15:00-21:00, шаг 30 минут, минимум 4 часа) ⏰",
        "e_cocktails": "Сколько коктейлей? (мин. 100, шаг 50) 🍹",
        "e_geo": "Выбери геолокацию снова:" if informal(context.user_data) else "Выберите геолокацию снова:",
        "e_phone": "Оставь телефон (11 цифр) 📞" if informal(context.user_data) else "Оставьте телефон (11 цифр) 📞",
    }

    if key in ("e_tariff", "e_geo"):
        if key == "e_tariff":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🥤 Монах Капуцин", callback_data="t_Монах Капуцин")],
                [InlineKeyboardButton("💼 Пастор", callback_data="t_Пастор")],
                [InlineKeyboardButton("✨ Святой", callback_data="t_Святой")],
                [InlineKeyboardButton("👑 Божество", callback_data="t_Божество")],
            ])
            await q.message.reply_text(prompts[key], reply_markup=kb)
            return ASK_TARIFF
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Москва", callback_data="geo_Москва")],
                [InlineKeyboardButton("Мос область", callback_data="geo_Мос область")],
                [InlineKeyboardButton("Другой город", callback_data="geo_Другой город")],
            ])
            await q.message.reply_text(prompts[key], reply_markup=kb)
            return ASK_GEO
    else:
        await q.message.reply_text(prompts[key])
        return EDIT_VALUE

async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("edit_key")
    val = update.message.text.strip()

    if key == "e_name":
        context.user_data["name"] = val
    elif key == "e_date":
        if not valid_date(val):
            await update.message.reply_text("Формат ДД.ММ.ГГГГ 🙂")
            return EDIT_VALUE
        context.user_data["date"] = val
    elif key == "e_event":
        context.user_data["event_type"] = val
    elif key == "e_guests":
        if not val.isdigit() or int(val) <= 0:
            await update.message.reply_text("Нужно число 🙂" if informal(context.user_data) else "Нужно число 🙂")
            return EDIT_VALUE
        context.user_data["guests"] = int(val)
    elif key == "e_time":
        if not valid_time_range(val, 4):
            await update.message.reply_text("Формат 15:00-21:00, шаг 30 мин, минимум 4 часа.")
            return EDIT_VALUE
        context.user_data["time_range"] = val
    elif key == "e_cocktails":
        if not val.isdigit() or int(val) < 100 or int(val) % 50 != 0:
            await update.message.reply_text("Минимум 100 и шаг 50 🙂")
            return EDIT_VALUE
        context.user_data["cocktails"] = int(val)
    elif key == "e_phone":
        if not valid_phone(val):
            await update.message.reply_text("11 цифр, например 89991234567 🙂")
            return EDIT_VALUE
        context.user_data["phone"] = val

    await update.message.reply_text(summary_text(context.user_data), parse_mode="Markdown")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Всё правильно", callback_data="ok")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data="edit")]
    ])
    await update.message.reply_text("Всё ли теперь верно? 🙂" if informal(context.user_data) else "Всё ли теперь верно? 🙂",
                                    reply_markup=kb)
    return CONFIRM_SUMMARY

# ----- Отправка заявки админу -----
async def send_application_to_admin(context: ContextTypes.DEFAULT_TYPE, user, user_data: dict):
    # данные пользователя
    full_name = (user.full_name or user.username or "Клиент").replace("<", "").replace(">", "")
    account_link = f'<a href="tg://user?id={user.id}">{full_name}</a>'
    user_lang = getattr(user, "language_code", None) or "ru"
    first_name = getattr(user, "first_name", "") or "HOLY MIXOLOG"

    # тело заявки
    text = summary_text(user_data).replace("*", "")  # для HTML
    admin_msg = (
        f"📩 Новая заявка\n\n"
        f"{text}\n\n"
        f"👤 Аккаунт: {account_link}\n"
        f"🔹 ID: {user.id}\n"
        f"🔹 First: {first_name}\n"
        f"🔹 Lang: {user_lang}"
    )
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_msg,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

# ----- /cancel -----
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. Если что — просто напиши /start 🙂", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_ty)],
            ASK_TY: [CallbackQueryHandler(set_ty)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_event)],
            ASK_EVENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tariffs)],
            ASK_TARIFF: [CallbackQueryHandler(choose_tariff)],
            ASK_TIME_RANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, time_range)],
            ASK_GUESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, guests_then_next)],
            ASK_START_TIME_BOZH: [MessageHandler(filters.TEXT & ~filters.COMMAND, start_time_bozh)],
            ASK_COCKTAILS_BOZH: [MessageHandler(filters.TEXT & ~filters.COMMAND, cocktails_bozh)],
            ASK_GEO: [CallbackQueryHandler(choose_geo)],
            ASK_ADDRESS_OR_HINT: [MessageHandler(filters.TEXT & ~filters.COMMAND, address_or_hint)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone)],
            CONFIRM_SUMMARY: [CallbackQueryHandler(confirm_or_edit)],
            EDIT_MENU: [CallbackQueryHandler(edit_menu)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__":
    main()
