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

# --- НАСТРОЙКИ ---
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

# --- ПРОВЕРКА НА АДМИНА ---
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
        if not user or user.is_bot:
            return await handler(event, data)
        if await is_user_admin(user.id):
            return await handler(event, data)
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

# --- АДМИН-КОМАНДЫ (С ПОДДЕРЖКОЙ ТОПИКОВ) ---

@router.message(Command("add"))
async def admin_add(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): return
    await state.clear()
    try:
        raw = message.text.split(maxsplit=1)[1]
        cat, name, desc = [p.strip() for p in raw.split("|")]
        supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.answer(f"✅ Проект <b>{name}</b> добавлен.", message_thread_id=message.message_thread_id, parse_mode="HTML")
    except:
        await message.answer("❌ Формат: /add категория | Название | Описание", message_thread_id=message.message_thread_id)

@router.message(Command("del"))
async def admin_delete(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): return
    await state.clear()
    try:
        name = message.text.split(maxsplit=1)[1].strip()
        supabase.table("projects").delete().eq("name", name).execute()
        await message.answer(f"🗑 Проект <b>{name}</b> удален.", message_thread_id=message.message_thread_id)
    except:
        await message.answer("❌ Формат: /del Название", message_thread_id=message.message_thread_id)

@router.message(Command("score"))
async def admin_score(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): return
    await state.clear()
    try:
        raw = message.text.split(maxsplit=1)[1]
        name, val = [p.strip() for p in raw.split("|")]
        res = supabase.table("projects").select("score").eq("name", name).single().execute().data
        new_score = res['score'] + int(val)
        supabase.table("projects").update({"score": new_score}).eq("name", name).execute()
        await message.answer(f"⚖️ Рейтинг {name}: <b>{new_score}</b>", message_thread_id=message.message_thread_id, parse_mode="HTML")
    except:
        await message.answer("❌ Формат: /score Название | число", message_thread_id=message.message_thread_id)

@router.message(Command("delrev"))
async def admin_delrev(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): return
    await state.clear()
    try:
        log_id = int(message.text.split()[1])
        rev = supabase.table("user_logs").select("*").eq("id", log_id).single().execute().data
        if rev:
            p = supabase.table("projects").select("score").eq("id", rev['project_id']).single().execute().data
            new_score = p['score'] - RATING_MAP.get(rev['rating_val'], 0)
            supabase.table("projects").update({"score": new_score}).eq("id", rev['project_id']).execute()
            supabase.table("user_logs").delete().eq("id", log_id).execute()
            await message.answer(f"🗑 Отзыв #{log_id} удален.", message_thread_id=message.message_thread_id)
    except:
        await message.answer("❌ Формат: /delrev ID", message_thread_id=message.message_thread_id)

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "<b>🏆 ТОП-5 ПРОЕКТОВ КМБП</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. <b>{p['name']}</b> — <code>{p['score']}</code>\n"
    else: text += "Список пуст.\n"
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    if not data: return await message.answer(f"В разделе '{message.text}' пусто.")
    for p in data:
        card = f"<b>{p['name']}</b>\n\n{p['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\nРейтинг: <b>{p['score']}</b>"
        await message.answer(card, reply_markup=project_inline_kb(p['id']), parse_mode="HTML")

@router.callback_query(F.data.startswith("rev_"))
async def rev_start(call: CallbackQuery, state: FSMContext):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    txt = "📝 <b>Изменение отзыва. Введите новый текст:</b>" if check.data else "💬 <b>Введите текст отзыва:</b>"
    await call.message.answer(txt, parse_mode="HTML"); await call.answer()

@router.message(ReviewState.waiting_for_text)
async def rev_text(message: Message, state: FSMContext):
    if message.text.startswith("/"): return 
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐"*i, callback_data=f"st_{i}")] for i in range(5, 0, -1)])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("🌟 <b>Выберите оценку:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("st_"), ReviewState.waiting_for_rate)
async def rev_end(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1]); data = await state.get_data(); p_id = data['p_id']
    old_rev = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    p = supabase.table("projects").select("score", "name").eq("id", p_id).single().execute().data
    
    if old_rev.data:
        new_score = p['score'] - RATING_MAP[old_rev.data[0]['rating_val']] + RATING_MAP[rate]
        supabase.table("user_logs").update({"review_text": data['txt'], "rating_val": rate}).eq("id", old_rev.data[0]['id']).execute()
        res_txt = "обновлен"
    else:
        new_score = p['score'] + RATING_MAP[rate]
        supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "review", "review_text": data['txt'], "rating_val": rate}).execute()
        res_txt = "добавлен"

    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    await call.message.edit_text(f"✅ Отзыв успешно {res_txt}!", parse_mode="HTML")
    
    # Отправка уведомления админам (если сообщение от юзера в ЛС, шлем в общую группу)
    await bot.send_message(ADMIN_GROUP_ID, f"📢 <b>Отзыв {res_txt}:</b> {p['name']}\nТекст: {data['txt']}\nОценка: {rate}/5", parse_mode="HTML")
    await state.clear(); await call.answer()

@router.callback_query(F.data.startswith("viewrev_"))
async def view_reviews(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    revs = supabase.table("user_logs").select("*").eq("project_id", p_id).eq("action_type", "review").order("created_at", desc=True).limit(5).execute().data
    if not revs: return await call.answer("Отзывов еще нет.", show_alert=True)
    text = "<b>💬 ПОСЛЕДНИЕ ОТЗЫВЫ:</b>\n\n"
    for r in revs:
        text += f"{'⭐' * r['rating_val']}\n<i>{r['review_text']}</i>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    await call.message.answer(text, parse_mode="HTML"); await call.answer()

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data: return await call.answer("Вы уже голосовали!", show_alert=True)
    res = supabase.table("projects").select("score").eq("id", p_id).single().execute().data
    supabase.table("projects").update({"score": res['score'] + 1}).eq("id", p_id).execute()
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    await call.answer("❤️ Голос учтен!")

async def main():
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
