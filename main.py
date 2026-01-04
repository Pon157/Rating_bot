import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from supabase import create_client, Client
from dotenv import load_dotenv

# --- НАСТРОЙКИ ТОПИКОВ (Замени цифры на ID из ссылок) ---
TOPIC_LOGS_ALL = 46 # Общий топик для ВСЕХ логов/отзывов

TOPICS_BY_CATEGORY = {
    "support_bots": 38,    # Топик для Ботов поддержки
    "support_admins": 41,  # Топик для Админов поддержки
    "lot_channels": 39,    # Топик для Каналов лотов
    "check_channels": 42,  # Топик для Каналов проверок
    "kmbp_channels": 40    # Топик для Каналов КМБП
}

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN") 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

CATEGORIES = {
    "support_bots": "Боты поддержки",
    "support_admins": "Админы поддержки",
    "lot_channels": "Каналы лотов",
    "check_channels": "Каналы проверок",
    "kmbp_channels": "Каналы КМБП"
}

RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}

class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

# --- ПРОВЕРКА ПРАВ (ПО ЧАТУ) ---
async def is_user_admin(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=ADMIN_GROUP_ID, user_id=user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception:
        return False

# --- MIDDLEWARE (БАН) ---
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot: return await handler(event, data)
        if await is_user_admin(user.id): return await handler(event, data)
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data: return
        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def main_kb():
    buttons = [[KeyboardButton(text=v)] for v in CATEGORIES.values()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def project_inline_kb(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Оценить/Изменить", callback_data=f"rev_{p_id}"),
         InlineKeyboardButton(text="❤️ Поддержать", callback_data=f"like_{p_id}")],
        [InlineKeyboardButton(text="💬 Посмотреть отзывы", callback_data=f"viewrev_{p_id}")]
    ])

# --- ПОЛУЧЕНИЕ ТОПИКА ДЛЯ ОТВЕТА ---
def get_thread_id(message: Message) -> int:
    """Получает thread_id из сообщения или возвращает 0"""
    return message.message_thread_id if message.message_thread_id else 0

# --- АДМИН-КОМАНДЫ (Работают в любом топике группы) ---

@router.message(Command("add"))
async def admin_add(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): return
    await state.clear()
    try:
        raw = message.text.split(maxsplit=1)[1]
        cat, name, desc = [p.strip() for p in raw.split("|")]
        supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.answer(
            f"✅ Проект <b>{name}</b> добавлен.", 
            parse_mode="HTML",
            message_thread_id=get_thread_id(message)
        )
    except Exception as e:
        logging.error(f"Ошибка в /add: {e}")
        await message.answer(
            "❌ Формат: /add категория | Название | Описание\nПример: /add support_bots | Бот Помощи | Отвечает на вопросы",
            message_thread_id=get_thread_id(message)
        )

@router.message(Command("del"))
async def admin_delete(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): return
    await state.clear()
    try:
        name = message.text.split(maxsplit=1)[1].strip()
        result = supabase.table("projects").delete().eq("name", name).execute()
        if len(result.data) > 0:
            await message.answer(
                f"🗑 Проект <b>{name}</b> удален.", 
                parse_mode="HTML",
                message_thread_id=get_thread_id(message)
            )
        else:
            await message.answer(
                f"❌ Проект <b>{name}</b> не найден.", 
                parse_mode="HTML",
                message_thread_id=get_thread_id(message)
            )
    except Exception as e:
        logging.error(f"Ошибка в /del: {e}")
        await message.answer(
            "❌ Формат: /del Название\nПример: /del Бот Помощи",
            message_thread_id=get_thread_id(message)
        )

@router.message(Command("score"))
async def admin_score(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): return
    await state.clear()
    try:
        raw = message.text.split(maxsplit=1)[1]
        name, val = [p.strip() for p in raw.split("|")]
        val = int(val)
        
        # Получаем текущий рейтинг
        result = supabase.table("projects").select("score, id").eq("name", name).execute()
        if not result.data:
            await message.answer(
                f"❌ Проект <b>{name}</b> не найден.",
                parse_mode="HTML",
                message_thread_id=get_thread_id(message)
            )
            return
            
        current_score = result.data[0]['score']
        project_id = result.data[0]['id']
        new_score = current_score + val
        
        # Обновляем рейтинг
        supabase.table("projects").update({"score": new_score}).eq("id", project_id).execute()
        
        await message.answer(
            f"⚖️ Рейтинг {name}: <b>{current_score} → {new_score}</b> (изменение: {val:+d})", 
            parse_mode="HTML",
            message_thread_id=get_thread_id(message)
        )
    except Exception as e:
        logging.error(f"Ошибка в /score: {e}")
        await message.answer(
            "❌ Формат: /score Название | число\nПример: /score Бот Помощи | 10",
            message_thread_id=get_thread_id(message)
        )

@router.message(Command("delrev"))
async def admin_delrev(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): return
    await state.clear()
    try:
        log_id = int(message.text.split()[1])
        
        # Получаем отзыв
        rev_result = supabase.table("user_logs").select("*").eq("id", log_id).execute()
        if not rev_result.data:
            await message.answer(
                f"❌ Отзыв #{log_id} не найден.",
                message_thread_id=get_thread_id(message)
            )
            return
            
        rev = rev_result.data[0]
        
        # Получаем проект
        project_result = supabase.table("projects").select("score, name").eq("id", rev['project_id']).execute()
        if project_result.data:
            p = project_result.data[0]
            rating_change = RATING_MAP.get(rev['rating_val'], 0)
            new_score = p['score'] - rating_change
            
            # Обновляем рейтинг проекта
            supabase.table("projects").update({"score": new_score}).eq("id", rev['project_id']).execute()
            
            # Удаляем отзыв
            supabase.table("user_logs").delete().eq("id", log_id).execute()
            
            await message.answer(
                f"🗑 Отзыв #{log_id} удален.\n"
                f"Проект: <b>{p['name']}</b>\n"
                f"Новый рейтинг: <b>{new_score}</b> (было: {p['score']})",
                parse_mode="HTML",
                message_thread_id=get_thread_id(message)
            )
        else:
            await message.answer(
                f"❌ Проект отзыва #{log_id} не найден.",
                message_thread_id=get_thread_id(message)
            )
    except Exception as e:
        logging.error(f"Ошибка в /delrev: {e}")
        await message.answer(
            "❌ Формат: /delrev ID\nПример: /delrev 123",
            message_thread_id=get_thread_id(message)
        )

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "<b>🏆 ТОП-5 ПРОЕКТОВ КМБП</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. <b>{p['name']}</b> — <code>{p['score']}</code>\n"
    else: 
        text += "Список пуст.\n"
    
    if message.chat.type == "private":
        await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    if not data: 
        await message.answer(f"В разделе '{message.text}' пусто.")
        return
    
    for p in data:
        card = f"<b>{p['name']}</b>\n\n{p['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\nРейтинг: <b>{p['score']}</b>"
        if message.chat.type == "private":
            await message.answer(card, reply_markup=project_inline_kb(p['id']), parse_mode="HTML")
        else:
            await message.answer(card, parse_mode="HTML")

@router.callback_query(F.data.startswith("rev_"))
async def rev_start(call: CallbackQuery, state: FSMContext):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    
    txt = "📝 <b>Изменение отзыва. Введите новый текст:</b>" if check.data else "💬 <b>Введите текст отзыва:</b>"
    
    if call.message.chat.type == "private":
        await call.message.answer(txt, parse_mode="HTML")
    else:
        await call.message.reply(txt, parse_mode="HTML")
    
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def rev_text(message: Message, state: FSMContext):
    if message.text and message.text.startswith("/"): 
        return 
    
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐"*i, callback_data=f"st_{i}")] for i in range(5, 0, -1)
    ])
    await state.set_state(ReviewState.waiting_for_rate)
    
    if message.chat.type == "private":
        await message.answer("🌟 <b>Выберите оценку:</b>", reply_markup=kb, parse_mode="HTML")
    else:
        await message.reply("🌟 <b>Выберите оценку:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("st_"), ReviewState.waiting_for_rate)
async def rev_end(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    p_id = data['p_id']
    
    old_rev = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    p = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
    
    if old_rev.data:
        new_score = p['score'] - RATING_MAP[old_rev.data[0]['rating_val']] + RATING_MAP[rate]
        supabase.table("user_logs").update({"review_text": data['txt'], "rating_val": rate}).eq("id", old_rev.data[0]['id']).execute()
        res_txt = "обновлен"
        log_id = old_rev.data[0]['id']
    else:
        new_score = p['score'] + RATING_MAP[rate]
        log = supabase.table("user_logs").insert({
            "user_id": call.from_user.id, 
            "project_id": p_id, 
            "action_type": "review", 
            "review_text": data['txt'], 
            "rating_val": rate
        }).execute()
        res_txt = "добавлен"
        log_id = log.data[0]['id']

    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    
    if call.message.chat.type == "private":
        await call.message.edit_text(f"✅ Отзыв успешно {res_txt}!", parse_mode="HTML")
    else:
        await call.message.reply(f"✅ Отзыв успешно {res_txt}!", parse_mode="HTML")
    
    # ФОРМИРУЕМ ЛОГ
    admin_text = (f"📢 <b>Отзыв {res_txt}:</b> {p['name']}\n"
                  f"Пользователь: @{call.from_user.username or call.from_user.id}\n"
                  f"Текст: <i>{data['txt']}</i>\n"
                  f"Оценка: {rate}/5\n"
                  f"Удалить: <code>/delrev {log_id}</code>")
    
    # 1. Шлем в общий топик логов
    if TOPIC_LOGS_ALL:
        await bot.send_message(ADMIN_GROUP_ID, admin_text, message_thread_id=TOPIC_LOGS_ALL, parse_mode="HTML")
    
    # 2. Шлем в топик конкретной категории
    cat_topic = TOPICS_BY_CATEGORY.get(p['category'])
    if cat_topic:
        await bot.send_message(ADMIN_GROUP_ID, admin_text, message_thread_id=cat_topic, parse_mode="HTML")

    await state.clear()
    await call.answer()

@router.callback_query(F.data.startswith("viewrev_"))
async def view_reviews(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    revs = supabase.table("user_logs").select("*").eq("project_id", p_id).eq("action_type", "review").order("created_at", desc=True).limit(5).execute().data
    if not revs: 
        await call.answer("Отзывов еще нет.", show_alert=True)
        return
    
    text = "<b>💬 ПОСЛЕДНИЕ ОТЗЫВЫ:</b>\n\n"
    for r in revs: 
        text += f"{'⭐' * r['rating_val']}\n<i>{r['review_text']}</i>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    if call.message.chat.type == "private":
        await call.message.answer(text, parse_mode="HTML")
    else:
        await call.message.reply(text, parse_mode="HTML")
    
    await call.answer()

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data: 
        await call.answer("Вы уже поддержали этот проект!", show_alert=True)
        return
    
    res = supabase.table("projects").select("score").eq("id", p_id).single().execute().data
    supabase.table("projects").update({"score": res['score'] + 1}).eq("id", p_id).execute()
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    await call.answer("❤️ Голос учтен!")

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())