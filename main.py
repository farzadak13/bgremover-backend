from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from rembg import remove, new_session
from PIL import Image
import io
import os
from supabase import create_client, Client
from jose import jwt
import httpx

app = FastAPI(title="BgRemover API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rhjmywdamcbrkvgvkkge.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Auth helper ───────────────────────────────────────────────────────────────
async def get_current_user(authorization: str = None):
    """Extract user from Bearer token"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="احراز هویت الزامی است")
    token = authorization.replace("Bearer ", "")
    try:
        user = supabase.auth.get_user(token)
        return user.user
    except Exception:
        raise HTTPException(status_code=401, detail="توکن نامعتبر است")


async def check_and_deduct_credits(user_id: str):
    """Check user has credits and deduct one"""
    result = supabase.table("profiles").select("credits, plan").eq("id", user_id).single().execute()
    profile = result.data
    if not profile:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")

    if profile["plan"] == "unlimited":
        return True

    if profile["credits"] <= 0:
        raise HTTPException(status_code=402, detail="اعتبار شما تمام شده است")

    supabase.table("profiles").update({"credits": profile["credits"] - 1}).eq("id", user_id).execute()
    supabase.table("operations").insert({"user_id": user_id, "status": "success"}).execute()
    return True


# ── Sessions cache ────────────────────────────────────────────────────────────
_sessions = {}

def get_session(model: str):
    if model not in _sessions:
        _sessions[model] = new_session(model)
    return _sessions[model]


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "message": "BgRemover API is running"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/remove-bg")
async def remove_background(
    file: UploadFile = File(...),
    model: str = "u2net",
    bg_color: str = "transparent",
    authorization: str = None,
):
    # Auth
    from fastapi import Header
    user = await get_current_user(authorization)
    await check_and_deduct_credits(user.id)

    # Validate file
    if file.content_type not in ["image/png", "image/jpeg", "image/webp", "image/jpg"]:
        raise HTTPException(status_code=400, detail="فرمت فایل پشتیبانی نمی‌شود")

    # Validate model
    allowed_models = [
        "u2net", "u2netp", "u2net_human_seg", "u2net_cloth_seg",
        "isnet-general-use", "isnet-anime",
        "birefnet-general", "birefnet-general-lite",
        "birefnet-portrait", "bria-rmbg",
    ]
    if model not in allowed_models:
        model = "u2net"

    contents = await file.read()

    try:
        session = get_session(model)
        output_bytes = remove(contents, session=session)
        result_img = Image.open(io.BytesIO(output_bytes)).convert("RGBA")

        # Apply background
        if bg_color == "white":
            bg = Image.new("RGBA", result_img.size, (255, 255, 255, 255))
            bg.paste(result_img, mask=result_img.split()[3])
            result_img = bg.convert("RGB")
            fmt, mime = "JPEG", "image/jpeg"
        elif bg_color.startswith("#") and len(bg_color) == 7:
            r = int(bg_color[1:3], 16)
            g = int(bg_color[3:5], 16)
            b = int(bg_color[5:7], 16)
            bg = Image.new("RGBA", result_img.size, (r, g, b, 255))
            bg.paste(result_img, mask=result_img.split()[3])
            result_img = bg.convert("RGB")
            fmt, mime = "JPEG", "image/jpeg"
        else:
            fmt, mime = "PNG", "image/png"

        buf = io.BytesIO()
        result_img.save(buf, format=fmt, quality=95)
        buf.seek(0)

        return Response(content=buf.getvalue(), media_type=mime)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"خطا در پردازش: {str(e)}")


@app.get("/profile")
async def get_profile(authorization: str = None):
    user = await get_current_user(authorization)
    result = supabase.table("profiles").select("*").eq("id", user.id).single().execute()
    return result.data


@app.get("/history")
async def get_history(authorization: str = None):
    user = await get_current_user(authorization)
    result = supabase.table("operations").select("*").eq("user_id", user.id).order("created_at", desc=True).limit(50).execute()
    return result.data
