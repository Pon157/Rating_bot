from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import logging
from typing import Optional, List, Dict, Any
import uuid
import time
from datetime import datetime, timedelta
import hashlib
import json

# Загрузка переменных окружения
load_dotenv()

app = FastAPI(title="Project Rating API", version="1.0.0")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене укажите конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Инициализация Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Константы
RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}
SESSION_DURATION_DAYS = 30

# Вспомогательные функции
def create_session_token(user_id: int) -> str:
    """Создание уникального токена сессии"""
    salt = os.urandom(32)
    data = f"{user_id}{time.time()}{salt.hex()}".encode()
    return hashlib.sha256(data).hexdigest()

def get_user_id_from_token(session_token: str) -> Optional[int]:
    """Получение user_id из токена сессии"""
    try:
        result = supabase.table("site_sessions")\
            .select("user_id, expires_at")\
            .eq("session_token", session_token)\
            .single()\
            .execute()
        
        if result.data:
            expires_at = datetime.fromisoformat(result.data['expires_at'].replace('Z', '+00:00'))
            if datetime.utcnow() < expires_at:
                return result.data['user_id']
    except Exception as e:
        logger.error(f"Error getting user from token: {e}")
    return None

# API endpoints
@app.get("/")
async def root():
    return {"message": "Project Rating API", "status": "online"}

@app.get("/api/projects")
async def get_projects(
    category: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("score", regex="^(score|name|created_at)$")
):
    """Получить список проектов"""
    try:
        query = supabase.table("projects").select("*")
        
        if category:
            query = query.eq("category", category)
        
        if sort_by == "score":
            query = query.order("score", desc=True)
        elif sort_by == "name":
            query = query.order("name")
        elif sort_by == "created_at":
            query = query.order("created_at", desc=True)
        
        query = query.range(offset, offset + limit - 1)
        
        result = query.execute()
        
        # Получаем фото для каждого проекта
        projects_with_photos = []
        for project in result.data:
            # Получаем фото проекта
            photo_result = supabase.table("project_photos")\
                .select("photo_file_id")\
                .eq("project_id", project['id'])\
                .order("updated_at", desc=True)\
                .limit(1)\
                .execute()
            
            project['photo'] = photo_result.data[0]['photo_file_id'] if photo_result.data else None
            projects_with_photos.append(project)
        
        # Получаем общее количество
        count_query = supabase.table("projects").select("*", count="exact")
        if category:
            count_query = count_query.eq("category", category)
        
        count_result = count_query.execute()
        total_count = count_result.count if hasattr(count_result, 'count') else 0
        
        return {
            "success": True,
            "data": projects_with_photos,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        logger.error(f"Error getting projects: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/projects/{project_id}")
async def get_project(project_id: int):
    """Получить информацию о конкретном проекте"""
    try:
        result = supabase.table("projects")\
            .select("*")\
            .eq("id", project_id)\
            .single()\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project = result.data
        
        # Получаем фото проекта
        photo_result = supabase.table("project_photos")\
            .select("photo_file_id")\
            .eq("project_id", project_id)\
            .order("updated_at", desc=True)\
            .limit(1)\
            .execute()
        
        project['photo'] = photo_result.data[0]['photo_file_id'] if photo_result.data else None
        
        # Получаем отзывы
        reviews_result = supabase.table("user_logs")\
            .select("id, user_id, review_text, rating_val, created_at")\
            .eq("project_id", project_id)\
            .eq("action_type", "review")\
            .order("created_at", desc=True)\
            .limit(10)\
            .execute()
        
        project['reviews'] = reviews_result.data if reviews_result.data else []
        
        # Получаем количество лайков
        likes_result = supabase.table("user_logs")\
            .select("*", count="exact")\
            .eq("project_id", project_id)\
            .eq("action_type", "like")\
            .execute()
        
        project['likes_count'] = likes_result.count if hasattr(likes_result, 'count') else 0
        
        # Получаем историю рейтинга
        history_result = supabase.table("rating_history")\
            .select("*")\
            .eq("project_id", project_id)\
            .order("created_at", desc=True)\
            .limit(5)\
            .execute()
        
        project['history'] = history_result.data if history_result.data else []
        
        return {"success": True, "data": project}
        
    except Exception as e:
        logger.error(f"Error getting project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/auth/start")
async def start_auth(user_id: int):
    """Начать процесс авторизации для веб-сайта"""
    try:
        # Проверяем, не забанен ли пользователь
        ban_check = supabase.table("banned_users")\
            .select("*")\
            .eq("user_id", user_id)\
            .execute()
        
        if ban_check.data:
            return {
                "success": False,
                "error": "banned",
                "reason": ban_check.data[0].get('reason', 'Не указана')
            }
        
        # Создаем или обновляем сессию
        session_token = create_session_token(user_id)
        expires_at = (datetime.utcnow() + timedelta(days=SESSION_DURATION_DAYS)).isoformat()
        
        supabase.table("site_sessions").upsert({
            "user_id": user_id,
            "session_token": session_token,
            "expires_at": expires_at
        }).execute()
        
        return {
            "success": True,
            "session_token": session_token,
            "expires_at": expires_at
        }
        
    except Exception as e:
        logger.error(f"Error starting auth for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/user/profile")
async def get_user_profile(session_token: str = Header(...)):
    """Получить профиль пользователя"""
    user_id = get_user_id_from_token(session_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    try:
        # Получаем отзывы пользователя
        reviews_result = supabase.table("user_logs")\
            .select("id, project_id, review_text, rating_val, created_at")\
            .eq("user_id", user_id)\
            .eq("action_type", "review")\
            .order("created_at", desc=True)\
            .execute()
        
        # Получаем лайки пользователя
        likes_result = supabase.table("user_logs")\
            .select("project_id, created_at")\
            .eq("user_id", user_id)\
            .eq("action_type", "like")\
            .execute()
        
        user_data = {
            "user_id": user_id,
            "reviews": reviews_result.data if reviews_result.data else [],
            "likes": [like['project_id'] for like in likes_result.data] if likes_result.data else []
        }
        
        return {"success": True, "data": user_data}
        
    except Exception as e:
        logger.error(f"Error getting profile for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/projects/{project_id}/review")
async def submit_review(
    project_id: int,
    review_data: dict,
    session_token: str = Header(...)
):
    """Оставить отзыв о проекте"""
    user_id = get_user_id_from_token(session_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    try:
        rating = review_data.get('rating')
        text = review_data.get('text', '')
        
        if rating not in [1, 2, 3, 4, 5]:
            raise HTTPException(status_code=400, detail="Invalid rating")
        
        # Получаем текущий проект
        project_result = supabase.table("projects")\
            .select("*")\
            .eq("id", project_id)\
            .single()\
            .execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project = project_result.data
        old_score = project['score']
        
        # Проверяем существующий отзыв
        existing_review = supabase.table("user_logs")\
            .select("*")\
            .eq("user_id", user_id)\
            .eq("project_id", project_id)\
            .eq("action_type", "review")\
            .execute()
        
        if existing_review.data:
            # Обновляем существующий отзыв
            old_rating = existing_review.data[0]['rating_val']
            rating_change = RATING_MAP[rating] - RATING_MAP[old_rating]
            
            supabase.table("user_logs")\
                .update({
                    "review_text": text,
                    "rating_val": rating
                })\
                .eq("id", existing_review.data[0]['id'])\
                .execute()
            
            log_id = existing_review.data[0]['id']
            change_type = "update_review"
            reason = f"Изменение отзыва: {old_rating}/5 → {rating}/5"
        else:
            # Создаем новый отзыв
            rating_change = RATING_MAP[rating]
            
            new_review = supabase.table("user_logs")\
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "action_type": "review",
                    "review_text": text,
                    "rating_val": rating
                })\
                .execute()
            
            log_id = new_review.data[0]['id']
            change_type = "new_review"
            reason = f"Новый отзыв: {rating}/5"
        
        new_score = old_score + rating_change
        
        # Обновляем рейтинг проекта
        supabase.table("projects")\
            .update({"score": new_score})\
            .eq("id", project_id)\
            .execute()
        
        # Добавляем в историю
        supabase.table("rating_history")\
            .insert({
                "project_id": project_id,
                "user_id": user_id,
                "change_type": change_type,
                "score_before": old_score,
                "score_after": new_score,
                "change_amount": rating_change,
                "reason": reason,
                "is_admin_action": False,
                "related_review_id": log_id
            })\
            .execute()
        
        return {
            "success": True,
            "message": "Review submitted successfully",
            "new_score": new_score,
            "change": rating_change
        }
        
    except Exception as e:
        logger.error(f"Error submitting review: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/projects/{project_id}/like")
async def toggle_like(
    project_id: int,
    session_token: str = Header(...)
):
    """Поставить/убрать лайк проекту"""
    user_id = get_user_id_from_token(session_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    try:
        # Проверяем существующий лайк
        existing_like = supabase.table("user_logs")\
            .select("*")\
            .eq("user_id", user_id)\
            .eq("project_id", project_id)\
            .eq("action_type", "like")\
            .execute()
        
        project_result = supabase.table("projects")\
            .select("*")\
            .eq("id", project_id)\
            .single()\
            .execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project = project_result.data
        old_score = project['score']
        
        if existing_like.data:
            # Удаляем лайк
            supabase.table("user_logs")\
                .delete()\
                .eq("id", existing_like.data[0]['id'])\
                .execute()
            
            new_score = old_score - 1
            change_type = "remove_like"
            change_amount = -1
            message = "Like removed"
        else:
            # Добавляем лайк
            supabase.table("user_logs")\
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "action_type": "like"
                })\
                .execute()
            
            new_score = old_score + 1
            change_type = "add_like"
            change_amount = 1
            message = "Like added"
        
        # Обновляем рейтинг проекта
        supabase.table("projects")\
            .update({"score": new_score})\
            .eq("id", project_id)\
            .execute()
        
        # Добавляем в историю
        supabase.table("rating_history")\
            .insert({
                "project_id": project_id,
                "user_id": user_id,
                "change_type": change_type,
                "score_before": old_score,
                "score_after": new_score,
                "change_amount": change_amount,
                "reason": "Лайк от пользователя",
                "is_admin_action": False
            })\
            .execute()
        
        return {
            "success": True,
            "message": message,
            "new_score": new_score,
            "liked": not existing_like.data
        }
        
    except Exception as e:
        logger.error(f"Error toggling like: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/categories")
async def get_categories():
    """Получить список категорий с количеством проектов"""
    try:
        result = supabase.table("projects")\
            .select("category, count", count="exact")\
            .group("category")\
            .execute()
        
        categories = []
        if result.data:
            for item in result.data:
                categories.append({
                    "name": item['category'],
                    "count": item['count']
                })
        
        return {"success": True, "data": categories}
        
    except Exception as e:
        logger.error(f"Error getting categories: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/stats")
async def get_stats():
    """Получить общую статистику"""
    try:
        # Общее количество проектов
        projects_result = supabase.table("projects")\
            .select("*", count="exact")\
            .execute()
        
        # Количество отзывов
        reviews_result = supabase.table("user_logs")\
            .select("*", count="exact")\
            .eq("action_type", "review")\
            .execute()
        
        # Количество лайков
        likes_result = supabase.table("user_logs")\
            .select("*", count="exact")\
            .eq("action_type", "like")\
            .execute()
        
        # Топ проектов
        top_projects_result = supabase.table("projects")\
            .select("*")\
            .order("score", desc=True)\
            .limit(5)\
            .execute()
        
        stats = {
            "total_projects": projects_result.count if hasattr(projects_result, 'count') else 0,
            "total_reviews": reviews_result.count if hasattr(reviews_result, 'count') else 0,
            "total_likes": likes_result.count if hasattr(likes_result, 'count') else 0,
            "top_projects": top_projects_result.data if top_projects_result.data else []
        }
        
        return {"success": True, "data": stats}
        
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/search")
async def search_projects(
    q: str = Query(..., min_length=2),
    limit: int = Query(10, ge=1, le=50)
):
    """Поиск проектов"""
    try:
        result = supabase.table("projects")\
            .select("*")\
            .or_(f"name.ilike.%{q}%,description.ilike.%{q}%")\
            .order("score", desc=True)\
            .limit(limit)\
            .execute()
        
        return {"success": True, "data": result.data if result.data else []}
        
    except Exception as e:
        logger.error(f"Error searching projects: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
