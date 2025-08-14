import asyncio
import json
import os
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, CallbackContext, CallbackQueryHandler, CommandHandler


# Load environment variables from .env if present
load_dotenv()


BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"


# Import content definitions (names and action templates)
from actions.people import PEOPLE, ACTIONS_ONE  # type: ignore
from actions.group import ACTIONS_PLURAL, WEIGHTS  # type: ignore


def read_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_config(cfg: Dict[str, Any]) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print("Ошибка записи config.json", exc)


def weighted_choice(weights: List[int]) -> int:
    total = sum(weights)
    if total <= 0:
        return 0
    r = os.urandom(8)
    # Convert to float in [0, 1)
    import struct

    rnd01 = struct.unpack("!Q", r)[0] / (2 ** 64)
    threshold = rnd01 * total
    for idx, w in enumerate(weights):
        if threshold < w:
            return idx
        threshold -= w
    return len(weights) - 1


def pick_unique(items: List[Any], k: int) -> List[Any]:
    if k >= len(items):
        return list(items)
    import random

    pool = list(items)
    result: List[Any] = []
    for _ in range(k):
        idx = random.randrange(len(pool))
        result.append(pool.pop(idx))
    return result


def format_names(selected: List[Dict[str, Any]]) -> str:
    names = [p["name"] for p in selected]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} и {names[1]}"
    last = names.pop()
    return f"{', '.join(names)} и {last}"


def random_from(items: List[str]) -> str:
    import random

    return items[random.randrange(len(items))]


def generate_event() -> str:
    action_type = weighted_choice(WEIGHTS)  # 0 = одиночный, иначе групповой
    if action_type == 0:
        person = pick_unique(PEOPLE, 1)[0]
        gender = "female" if person.get("gender") == "female" else "male"
        tpl = random_from(ACTIONS_ONE[gender])
        return tpl.replace("{name}", person["name"])
    else:
        count = __import__("random").randint(2, 4)
        chosen = pick_unique(PEOPLE, count)
        names_str = format_names(chosen)
        tpl = random_from(ACTIONS_PLURAL)
        if "{names}" in tpl:
            return tpl.replace("{names}", names_str)
        return f"{names_str} {tpl}"


def get_target_chat_id() -> str | None:
    chat_id = os.getenv("CHAT_ID")
    if chat_id:
        return chat_id
    cfg = read_config()
    cid = cfg.get("chatId")
    return str(cid) if cid is not None else None


async def send_event_to_target(application: Application) -> None:
    chat_id = get_target_chat_id()
    if not chat_id:
        print("Чат не задан — используй /register или укажи CHAT_ID в .env")
        return
    text = generate_event()
    try:
        await application.bot.send_message(chat_id=chat_id, text=text)
        print("Отправлено событие:", text)
    except Exception as exc:
        print("Ошибка отправки:", exc)


# Telegram command handlers
async def cmd_start(update: Update, context: CallbackContext) -> None:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="Сгенерировать действие", callback_data="generate")]]
    )
    await update.effective_message.reply_text(
        "Привет! Я бот-генератор событий для ПВЗ.\n\n"
        "/generate — сгенерировать событие\n"
        "/register — включить автопостинг\n"
        "/unregister — выключить автопостинг",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


async def cmd_generate(update: Update, context: CallbackContext) -> None:
    await update.effective_message.reply_text(generate_event())


async def cmd_register(update: Update, context: CallbackContext) -> None:
    cfg = read_config()
    cfg["chatId"] = update.effective_chat.id
    write_config(cfg)
    await update.effective_message.reply_text("Этот чат зарегистрирован для автопостинга.")


async def cmd_unregister(update: Update, context: CallbackContext) -> None:
    cfg = read_config()
    current_id = str(update.effective_chat.id)
    stored = str(cfg.get("chatId")) if cfg.get("chatId") is not None else None
    if stored and stored == current_id:
        cfg.pop("chatId", None)
        write_config(cfg)
        await update.effective_message.reply_text("Автопостинг отключён.")
    else:
        await update.effective_message.reply_text("Этот чат не был зарегистрирован.")


async def handle_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        if query.data == "generate":
            await query.message.reply_text(generate_event())
            await query.answer(text="Сгенерировано")
        else:
            await query.answer(text="Неизвестная команда")
    except Exception as exc:
        print(exc)
        try:
            await query.answer(text="Ошибка")
        except Exception:
            pass


# Scheduler utilities
async def _job_send_event(context: CallbackContext) -> None:
    await send_event_to_target(context.application)


def schedule_repeating_job(application: Application) -> None:
    minutes_env = os.getenv("INTERVAL_MINUTES")
    hours_env = os.getenv("INTERVAL_HOURS")

    interval_seconds: float | None = None
    try:
        if minutes_env is not None and float(minutes_env) > 0:
            interval_seconds = float(minutes_env) * 60
            print(f"Интервал: {minutes_env} минут")
        elif hours_env is not None and float(hours_env) > 0:
            interval_seconds = float(hours_env) * 60 * 60
            print(f"Интервал: {hours_env} часов")
    except Exception:
        interval_seconds = None

    if interval_seconds is None:
        interval_seconds = 12 * 60 * 60
        print("Интервал не задан — по умолчанию 12 часов")

    if interval_seconds <= 0:
        print("Автопостинг отключён.")
        return

    send_on_start = str(os.getenv("SEND_ON_START", "false")).lower() in {"1", "true", "yes"}
    first_run = 0 if send_on_start else interval_seconds
    application.job_queue.run_repeating(_job_send_event, interval=interval_seconds, first=first_run)


async def main_async() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("ERROR: Укажи BOT_TOKEN в .env")
        raise SystemExit(1)

    application: Application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("generate", cmd_generate))
    application.add_handler(CommandHandler("register", cmd_register))
    application.add_handler(CommandHandler("unregister", cmd_unregister))
    application.add_handler(CallbackQueryHandler(handle_callback))

    schedule_repeating_job(application)
    print("Bot started. Use /start, /generate, /register.")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("Остановка бота…")


