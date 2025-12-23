# main.py
import asyncio
import logging
import os
from datetime import datetime, date

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton




from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db import (
    init_db,
    get_or_create_user,
    add_payment,
    get_payments_for_user,
    get_month_total_for_user,
    get_remaining_total_for_user,
    get_payments_for_day,
    get_payment_by_id,
    delete_payment,
    update_payment,
    cleanup_inactive_payments,  # <-- добавили
)

from dotenv import load_dotenv
from html import escape

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="➕ Добавить платёж"),
        ],
        [
            KeyboardButton(text="📋 Список"),
            KeyboardButton(text="📆 Сумма в месяц"),
        ],
        [
            KeyboardButton(text="💰 Остаток"),
        ],
    ],
    resize_keyboard=True,  # чтобы клавиатура была компактной
)



class AddPaymentForm(StatesGroup):
    title = State()
    amount = State()
    day = State()

class EditPaymentForm(StatesGroup):
    new_title = State()
    new_amount = State()
    new_day = State()

async def cmd_cleanup(message: Message):
    """
    Удаляет все неактивные платежи (active = 0) из БД.
    """
    # По-хорошему, имеет смысл ограничить команду только админом/тобой.
    # Пока сделаем для всех, кто её вызвал.
    deleted = cleanup_inactive_payments()
    if deleted == 0:
        await message.answer("Не найдено неактивных платежей для очистки.")
    else:
        await message.answer(f"Удалено неактивных платежей: {deleted}")


async def cmd_start(message: Message):
    get_or_create_user(message.from_user.id)
    text = (
        "Привет! Я бот для управления регулярными платежами.\n\n"
        "Можешь пользоваться кнопками внизу или командами:\n"
        "/add — добавить платеж\n"
        "/list — список платежей\n"
        "/month — общая сумма в месяц\n"
        "/rest — сумма оставшихся платежей в этом месяце\n"
    )
    await message.answer(text, reply_markup=main_kb)



async def cmd_add(message: Message, state: FSMContext):
    user_id = get_or_create_user(message.from_user.id)
    await state.update_data(user_id=user_id)
    await message.answer("Введите название платежа (например: Аренда, Интернет):")
    await state.set_state(AddPaymentForm.title)


async def add_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("Введите сумму (например: 15000.50):")
    await state.set_state(AddPaymentForm.amount)


async def add_amount(message: Message, state: FSMContext):
    text = message.text.replace(",", ".").strip()
    try:
        amount = float(text)
    except ValueError:
        await message.answer("Не получилось распознать сумму. Попробуйте ещё раз (пример: 15000.50)")
        return

    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля. Введите ещё раз:")
        return

    await state.update_data(amount=amount)
    await message.answer("Введите число месяца, когда нужно платить (1–31):")
    await state.set_state(AddPaymentForm.day)


async def add_day(message: Message, state: FSMContext):
    try:
        day = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно число от 1 до 31. Попробуйте ещё раз:")
        return

    if not 1 <= day <= 31:
        await message.answer("Число месяца должно быть от 1 до 31. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    user_id = data["user_id"]
    title = data["title"]
    amount = data["amount"]

    add_payment(user_id, title, amount, day)
    await message.answer(f"Платёж добавлен:\n\n{title}: {amount:.2f} ₽, каждый месяц {day}-го числа")
    await state.clear()

def build_confirm_delete_kb(payment_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения удаления: Да / Нет.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да",
                    callback_data=f"confirm_del_yes:{payment_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=f"confirm_del_no:{payment_id}",
                ),
            ]
        ]
    )

def build_payment_text(p) -> str:
    """
    Текст сообщения для платежа.
    p — это row из БД (sqlite3.Row).
    """
    return f"{p['title']} — {p['amount']:.2f} ₽, {p['day_of_month']}-го числа"


def build_payment_inline_kb(payment_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура под платежом: редактировать по полям / удалить.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Название",
                    callback_data=f"edit_title:{payment_id}",
                ),
                InlineKeyboardButton(
                    text="💸 Сумма",
                    callback_data=f"edit_amount:{payment_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📅 Дата",
                    callback_data=f"edit_day:{payment_id}",
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"del:{payment_id}",
                ),
            ],
        ]
    )

def parse_payment_id_from_cb(callback: CallbackQuery) -> int | None:
    data = callback.data or ""
    try:
        _prefix, pid_str = data.split(":", 1)
        return int(pid_str)
    except Exception:
        return None


def build_list_edit_kb() -> InlineKeyboardMarkup:
    """
    Клавиатура под сводным списком: одна кнопка для входа в режим редактирования.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать / удалять",
                    callback_data="open_edit_list",
                )
            ]
        ]
    )


async def cmd_list(message: Message):
    user_id = get_or_create_user(message.from_user.id)
    payments = get_payments_for_user(user_id)
    if not payments:
        await message.answer("У вас пока нет регулярных платежей. Используйте /add, чтобы добавить.")
        return

    lines = []
    for p in payments:
        lines.append(build_payment_text(p))

    text = "Ваши регулярные платежи:\n\n" + "\n".join(lines)
    kb = build_list_edit_kb()
    await message.answer(text, reply_markup=kb)



async def send_payments_as_messages(message: Message, user_id: int):
    """
    Отправляет платежи по одному сообщению (как сейчас).
    """
    payments = get_payments_for_user(user_id)
    if not payments:
        await message.answer("У вас пока нет регулярных платежей. Используйте /add, чтобы добавить.")
        return

    await message.answer("Ваши регулярные платежи для редактирования:")

    for p in payments:
        text = build_payment_text(p)
        kb = build_payment_inline_kb(p["id"])
        await message.answer(text, reply_markup=kb)

async def cb_open_edit_list(callback: CallbackQuery):
    """
    Нажата кнопка '✏️ Редактировать / удалять' под сводным списком.
    Отправляем платежи по одному сообщению, каждый с собственными кнопками.
    """
    user_id = get_or_create_user(callback.from_user.id)

    # Можно не трогать исходное сводное сообщение, просто дополнительно отправить детальный список
    await send_payments_as_messages(callback.message, user_id)

    await callback.answer()


async def cmd_month(message: Message):
    user_id = get_or_create_user(message.from_user.id)
    total = get_month_total_for_user(user_id)
    await message.answer(f"Общая сумма ваших регулярных платежей в месяц: {total:.2f} ₽")


async def cmd_rest(message: Message):
    user_id = get_or_create_user(message.from_user.id)
    today = date.today()
    remaining = get_remaining_total_for_user(user_id, today=today)
    await message.answer(
        f"Сумма оставшихся платежей до конца месяца (включая сегодня): {remaining:.2f} ₽"
    )

async def cmd_del(message: Message):
    user_tg_id = message.from_user.id
    user_id = get_or_create_user(user_tg_id)

    parts = message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /del ID\nНапример: /del 3")
        return

    payment_id = int(parts[1])
    ok = delete_payment(user_id, payment_id)
    if ok:
        await message.answer(f"Платёж с ID #{payment_id} удалён (деактивирован).")
    else:
        await message.answer("Платёж не найден или уже удалён.")

async def cb_delete_payment(callback: CallbackQuery):
    """
    Первый шаг: нажали '🗑 Удалить' – спрашиваем подтверждение.
    callback_data: del:<id>
    """
    user_id = get_or_create_user(callback.from_user.id)

    data = callback.data or ""
    try:
        _prefix, pid_str = data.split(":", 1)
        payment_id = int(pid_str)
    except Exception:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        await callback.answer("Платёж не найден или уже удалён.", show_alert=True)
        return

    text = (
        f"Удалить платёж #{payment_id}?\n"
        f"{payment['title']} — {payment['amount']:.2f} ₽, {payment['day_of_month']}-го числа."
    )
    kb = build_confirm_delete_kb(payment_id)

    try:
        # редактируем исходное сообщение с платежом
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        # если не удалось (например, уже редактировали) – отправим новое сообщение
        await callback.message.answer(text, reply_markup=kb)

    await callback.answer()

async def cb_confirm_delete_yes(callback: CallbackQuery):
    """
    Подтверждение удаления: Да.
    callback_data: confirm_del_yes:<id>
    """
    user_id = get_or_create_user(callback.from_user.id)

    data = callback.data or ""
    try:
        _prefix, pid_str = data.split(":", 1)
        payment_id = int(pid_str)
    except Exception:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    ok = delete_payment(user_id, payment_id)
    if ok:
        await callback.answer("Платёж удалён.")
        try:
            await callback.message.edit_text(
                f"Платёж #{payment_id} удалён.",
                reply_markup=None,
            )
        except Exception:
            pass
    else:
        await callback.answer("Платёж не найден или уже удалён.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


async def cb_confirm_delete_no(callback: CallbackQuery):
    """
    Отмена удаления: Нет.
    callback_data: confirm_del_no:<id>
    """
    user_id = get_or_create_user(callback.from_user.id)

    data = callback.data or ""
    try:
        _prefix, pid_str = data.split(":", 1)
        payment_id = int(pid_str)
    except Exception:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        # Платёж уже удалён или недоступен — просто убираем клавиатуру подтверждения
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    # Восстанавливаем исходный вид: текст + клавиатура «Редактировать / Удалить»
    text = build_payment_text(payment)
    kb = build_payment_inline_kb(payment_id)

    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        # Если не получилось отредактировать (редко), отправим новое сообщение
        await callback.message.answer(text, reply_markup=kb)

    await callback.answer("Удаление отменено.")



async def cmd_edit(message: Message, state: FSMContext):
    user_tg_id = message.from_user.id
    user_id = get_or_create_user(user_tg_id)

    parts = message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /edit ID\nНапример: /edit 3")
        return

    payment_id = int(parts[1])
    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        await message.answer("Платёж с таким ID не найден.")
        return

    # Сохраняем в состояние, что редактируем
    await state.update_data(
        edit_payment_id=payment_id,
        edit_title=payment["title"],
        edit_amount=payment["amount"],
        edit_day=payment["day_of_month"],
    )

    text = (
        f"Редактирование платежа #{payment_id}:\n"
        f"{payment['title']} — {payment['amount']:.2f} ₽, {payment['day_of_month']}-го числа.\n\n"
        "Что хотите изменить?\n"
        "Напишите: название / сумма / дата\n"
        "Или: всё — чтобы изменить всё по шагам."
    )
    await message.answer(text)
    await state.set_state(EditPaymentForm.waiting_for_field)

async def cb_edit_payment(callback: CallbackQuery, state: FSMContext):
    """
    Обработка нажатия 'Редактировать'.
    callback_data: edit:<id>
    """
    user_id = get_or_create_user(callback.from_user.id)

    data = callback.data or ""
    try:
        _prefix, pid_str = data.split(":", 1)
        payment_id = int(pid_str)
    except Exception:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

async def cb_edit_title(callback: CallbackQuery, state: FSMContext):
    user_id = get_or_create_user(callback.from_user.id)
    payment_id = parse_payment_id_from_cb(callback)
    if payment_id is None:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    await state.update_data(edit_payment_id=payment_id)
    await callback.message.answer(
        f"Текущее название: {payment['title']}\n"
        "Введите новое название:"
    )
    await callback.answer()
    await state.set_state(EditPaymentForm.new_title)


async def cb_edit_amount(callback: CallbackQuery, state: FSMContext):
    user_id = get_or_create_user(callback.from_user.id)
    payment_id = parse_payment_id_from_cb(callback)
    if payment_id is None:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    await state.update_data(edit_payment_id=payment_id)
    await callback.message.answer(
        f"Текущая сумма: {payment['amount']:.2f} ₽\n"
        "Введите новую сумму (например: 1500.50):"
    )
    await callback.answer()
    await state.set_state(EditPaymentForm.new_amount)


async def cb_edit_day(callback: CallbackQuery, state: FSMContext):
    user_id = get_or_create_user(callback.from_user.id)
    payment_id = parse_payment_id_from_cb(callback)
    if payment_id is None:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    await state.update_data(edit_payment_id=payment_id)
    await callback.message.answer(
        f"Текущая дата: {payment['day_of_month']}-го числа.\n"
        "Введите новое число месяца (1–31):"
    )
    await callback.answer()
    await state.set_state(EditPaymentForm.new_day)



    # Сохраняем в состояние
    await state.update_data(
        edit_payment_id=payment_id,
        edit_title=payment["title"],
        edit_amount=payment["amount"],
        edit_day=payment["day_of_month"],
    )

    text = (
        f"Редактирование платежа #{payment_id}:\n"
        f"{payment['title']} — {payment['amount']:.2f} ₽, {payment['day_of_month']}-го числа.\n\n"
        "Что хотите изменить?\n"
        "Напишите: название / сумма / дата\n"
        "Или: всё — чтобы изменить всё по шагам."
    )
    await callback.message.answer(text)
    await callback.answer()  # закрыть "часики" на кнопке
    await state.set_state(EditPaymentForm.waiting_for_field)


async def edit_set_title(message: Message, state: FSMContext):
    new_title = message.text.strip()
    if not new_title:
        await message.answer("Название не может быть пустым. Введите ещё раз:")
        return

    data = await state.get_data()
    payment_id = data.get("edit_payment_id")
    if payment_id is None:
        await message.answer("Не удалось определить платёж для редактирования.")
        await state.clear()
        return

    user_id = get_or_create_user(message.from_user.id)
    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        await message.answer("Платёж не найден или уже удалён.")
        await state.clear()
        return

    ok = update_payment(
        user_id=user_id,
        payment_id=payment_id,
        title=new_title,
        amount=payment["amount"],
        day_of_month=payment["day_of_month"],
    )
    if ok:
        await message.answer(f"Название платежа #{payment_id} обновлено на: {new_title}")
    else:
        await message.answer("Не удалось обновить платёж.")

    await state.clear()


async def edit_set_amount(message: Message, state: FSMContext):
    text = message.text.replace(",", ".").strip()
    try:
        new_amount = float(text)
    except ValueError:
        await message.answer("Не получилось распознать сумму. Попробуйте ещё раз (пример: 1500.50)")
        return

    if new_amount <= 0:
        await message.answer("Сумма должна быть больше нуля. Введите ещё раз:")
        return

    data = await state.get_data()
    payment_id = data.get("edit_payment_id")
    if payment_id is None:
        await message.answer("Не удалось определить платёж для редактирования.")
        await state.clear()
        return

    user_id = get_or_create_user(message.from_user.id)
    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        await message.answer("Платёж не найден или уже удалён.")
        await state.clear()
        return

    ok = update_payment(
        user_id=user_id,
        payment_id=payment_id,
        title=payment["title"],
        amount=new_amount,
        day_of_month=payment["day_of_month"],
    )
    if ok:
        await message.answer(f"Сумма платежа #{payment_id} обновлена на: {new_amount:.2f} ₽")
    else:
        await message.answer("Не удалось обновить платёж.")

    await state.clear()


async def edit_set_day(message: Message, state: FSMContext):
    try:
        new_day = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно число от 1 до 31. Попробуйте ещё раз:")
        return

    if not 1 <= new_day <= 31:
        await message.answer("Число месяца должно быть от 1 до 31. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    payment_id = data.get("edit_payment_id")
    if payment_id is None:
        await message.answer("Не удалось определить платёж для редактирования.")
        await state.clear()
        return

    user_id = get_or_create_user(message.from_user.id)
    payment = get_payment_by_id(user_id, payment_id)
    if not payment:
        await message.answer("Платёж не найден или уже удалён.")
        await state.clear()
        return

    ok = update_payment(
        user_id=user_id,
        payment_id=payment_id,
        title=payment["title"],
        amount=payment["amount"],
        day_of_month=new_day,
    )
    if ok:
        await message.answer(f"Дата платежа #{payment_id} обновлена на: {new_day}-е число")
    else:
        await message.answer("Не удалось обновить платёж.")

    await state.clear()



# --- Планировщик напоминаний ---


async def send_daily_reminders(bot: Bot):
    today = date.today()
    day = today.day

    rows = get_payments_for_day(day)
    if not rows:
        return

    for row in rows:
        tg_id = row["tg_id"]
        title = row["title"]
        amount = row["amount"]
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=f"Напоминание о платеже:\n\n{title} — {amount:.2f} ₽ сегодня.",
            )
        except Exception as e:
            logging.error(f"Ошибка отправки напоминания {tg_id}: {e}")

# --- Обработчики кнопок меню ---


async def btn_add(message: Message, state: FSMContext):
    # просто вызываем логику /add
    await cmd_add(message, state)


async def btn_list(message: Message):
    await cmd_list(message)


async def btn_month(message: Message):
    await cmd_month(message)


async def btn_rest(message: Message):
    await cmd_rest(message)


async def main():
    init_db()

    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_add, Command("add"))
    dp.message.register(cmd_list, Command("list"))
    dp.message.register(cmd_month, Command("month"))
    dp.message.register(cmd_rest, Command("rest"))
    dp.message.register(cmd_del, Command("del"))
    dp.message.register(cmd_edit, Command("edit"))
    dp.message.register(cmd_cleanup, Command("cleanup"))  # <-- добавили


    # обработчики кнопок меню (по тексту)
    dp.message.register(btn_add, F.text == "➕ Добавить платёж")
    dp.message.register(btn_list, F.text == "📋 Список")
    dp.message.register(btn_month, F.text == "📆 Сумма в месяц")
    dp.message.register(btn_rest, F.text == "💰 Остаток")

    # callback-и под платежами
    dp.callback_query.register(cb_delete_payment, F.data.startswith("del:"))
    dp.callback_query.register(cb_edit_payment, F.data.startswith("edit:"))
    dp.callback_query.register(cb_confirm_delete_yes, F.data.startswith("confirm_del_yes:"))
    dp.callback_query.register(cb_confirm_delete_no, F.data.startswith("confirm_del_no:"))
    dp.callback_query.register(cb_edit_title, F.data.startswith("edit_title:"))
    dp.callback_query.register(cb_edit_amount, F.data.startswith("edit_amount:"))
    dp.callback_query.register(cb_edit_day, F.data.startswith("edit_day:"))
    dp.callback_query.register(cb_open_edit_list, F.data == "open_edit_list")


    # FSM-обработчики
    dp.message.register(add_title, AddPaymentForm.title)
    dp.message.register(add_amount, AddPaymentForm.amount)
    dp.message.register(add_day, AddPaymentForm.day)

    # FSM редактирования (только по полям)
    dp.message.register(edit_set_title, EditPaymentForm.new_title)
    dp.message.register(edit_set_amount, EditPaymentForm.new_amount)
    dp.message.register(edit_set_day, EditPaymentForm.new_day)


    # FSM-обработчики добавления
    dp.message.register(add_title, AddPaymentForm.title)
    dp.message.register(add_amount, AddPaymentForm.amount)
    dp.message.register(add_day, AddPaymentForm.day)



    # Планировщик
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")  # поменяйте под свой часовой пояс
    # Каждый день в 09:00 отправляем напоминания
    scheduler.add_job(
        send_daily_reminders,
        trigger=CronTrigger(hour=9, minute=0),
        args=(bot,),
        id="daily_reminders",
        replace_existing=True,
    )
    scheduler.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
