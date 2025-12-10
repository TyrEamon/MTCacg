import os
import asyncio
import logging
import time
from io import BytesIO
import aiohttp
import boto3
from aiogram import Bot
from aiogram.types import BufferedInputFile
from dotenv import load_dotenv

# 尝试导入 pixivpy3
try:
    from pixivpy3 import AppPixivAPI
    HAS_PIXIV = True
except ImportError:
    HAS_PIXIV = False
    print("⚠️ 未检测到 pixivpy3，Pixiv 功能不可用")

load_dotenv()

# --- 日志配置 ---
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 1. 变量读取函数 (兼容空格/下划线) ---
def get_env(key, default=None):
    # 优先读标准key，读不到就读把下划线换成空格的key
    val = os.getenv(key) or os.getenv(key.replace("_", " "))
    if val: return val.strip()
    return default

# --- 2. 核心变量配置 ---
BOT_TOKEN = get_env("BOT_TOKEN")
CHANNEL_ID = get_env("CHANNEL_ID")

# Cloudflare 相关
CF_ACCOUNT_ID = get_env("CLOUDFLARE_ACCOUNT_ID") or get_env("CF_ACCOUNT_ID")
CF_API_TOKEN = get_env("CLOUDFLARE_API_TOKEN") or get_env("CF_API_TOKEN")
D1_DB_ID = get_env("D1_DATABASE_ID") # 你的 D1 ID 就在这里读取

# R2 相关
R2_ACCESS_KEY = get_env("R2_ACCESS_KEY_ID")
R2_SECRET_KEY = get_env("R2_SECRET_ACCESS_KEY")
R2_BUCKET = get_env("R2_BUCKET_NAME")

# Pixiv 相关
PIXIV_PHPSESSID = get_env("PIXIV_PHPSESSID") # 你的 PHPSESSID
PIXIV_REFRESH_TOKEN = get_env("PIXIV_REFRESH_TOKEN")
PIXIV_ARTIST_IDS = get_env("PIXIV_ARTIST_IDS", "") # 你的画师列表
PIXIV_LIMIT = int(get_env("PIXIV_LIMIT", 3))

# Yande 相关
YANDE_LIMIT = int(get_env("YANDE_LIMIT", 1))

# --- 3. 启动检查 ---
required_vars = [BOT_TOKEN, CHANNEL_ID, CF_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET, D1_DB_ID]
if not all(required_vars):
    logger.error("❌ 缺少核心变量！请检查 Leaflow 配置。")
    exit(1)

R2_ENDPOINT = f"https://{CF_ACCOUNT_ID}.r2.cloudflarestorage.com"
bot = Bot(token=BOT_TOKEN)
s3_client = boto3.client('s3', endpoint_url=R2_ENDPOINT,
                         aws_access_key_id=R2_ACCESS_KEY,
                         aws_secret_access_key=R2_SECRET_KEY)

# --- 4. 核心逻辑函数 ---

def upload_to_r2_sync(file_data, filename):
    try:
        file_data.seek(0)
        s3_client.upload_fileobj(file_data, R2_BUCKET, filename, ExtraArgs={'ContentType': 'image/jpeg'})
        logger.info(f"☁️ R2 上传成功: {filename}")
        return True
    except Exception as e:
        logger.error(f"❌ R2 上传失败: {e}")
        return False

async def save_to_d1(post_id, file_name, caption, tags, source):
    """写入 D1"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DB_ID}/query"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    
    # 依然使用 INSERT OR IGNORE 防止重复报错
    sql = "INSERT OR IGNORE INTO images (id, file_name, caption, tags, created_at) VALUES (?, ?, ?, ?, ?)"
    params = [str(post_id), file_name, caption, tags, int(time.time())]
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={"sql": sql, "params": params}) as resp:
            if resp.status == 200:
                logger.info(f"💾 D1 写入成功: {post_id}")
            else:
                logger.error(f"❌ D1 写入失败: {await resp.text()}")

async def process_image(img_buffer, post_id, tags, caption, source):
    """通用处理: TG -> R2 -> D1"""
    try:
        file_name = f"{source}_{post_id}.jpg"
        
        # 1. 发送 TG (关键：使用 BufferedInputFile 修复文件名问题)
        tg_file = BufferedInputFile(img_buffer.getvalue(), filename=file_name)
        await bot.send_photo(chat_id=int(CHANNEL_ID), photo=tg_file, caption=caption)
        logger.info(f"✅ TG 发送成功: {post_id}")
        
        # 2. 上传 R2
        await asyncio.to_thread(upload_to_r2_sync, img_buffer, file_name)
        
        # 3. 写入 D1
        await save_to_d1(post_id, file_name, caption, tags, source)
        
    except Exception as e:
        logger.error(f"⚠️ 图片处理失败 {post_id}: {e}")

# --- 5. 爬虫逻辑 ---

async def fetch_pixiv():
    """Pixiv 抓取逻辑 (优先使用 PHPSESSID 和 ARTIST_IDS)"""
    if not HAS_PIXIV: return

    logger.info("🔍 正在检查 Pixiv...")
    api = AppPixivAPI()
    
    # --- 登录逻辑 ---
    try:
        if PIXIV_REFRESH_TOKEN:
            api.auth(refresh_token=PIXIV_REFRESH_TOKEN)
            logger.info("✅ Pixiv: 使用 Refresh Token 登录成功")
        elif PIXIV_PHPSESSID:
            # 使用 PHPSESSID 这种方式其实是模拟网页请求，pixivpy3 原生不支持直接传 cookie 给 auth
            # 但我们可以直接给 requests session 设置 cookie
            api.requests_kwargs.update({'headers': {'User-Agent': 'PixivAndroidApp/5.0.155'}})
            # 这是一个 hack，通常 pixivpy3 需要 token。
            # 如果你只有 PHPSESSID，建议使用 requests 直接爬，或者寻找支持 cookie 的库。
            # 但既然你用了 pixivpy3，我们假设你的 PHPSESSID 能用在 header 里。
            # 注意：pixivpy3 强依赖 OAuth token，仅有 cookie 可能无法调用所有 API。
            # 暂时尝试直接调用，如果报错，说明 pixivpy3 必须要有 token。
            logger.warning("⚠️ Pixiv: 仅检测到 PHPSESSID，API 调用可能受限。强烈建议获取 Refresh Token。")
        else:
            logger.warning("⚠️ Pixiv: 未配置 Token 或 Cookie，跳过。")
            return
    except Exception as e:
        logger.error(f"Pixiv 登录异常: {e}")
        return

    # --- 抓取逻辑 ---
    target_illusts = []

    # 1. 优先抓取指定画师
    if PIXIV_ARTIST_IDS:
        artist_ids = [x.strip() for x in PIXIV_ARTIST_IDS.split(',') if x.strip()]
        logger.info(f"🎨 正在抓取指定画师: {artist_ids}")
        for uid in artist_ids:
            try:
                # 注意：如果仅有 cookie，这一步可能会 401 Unauthorized
                json_result = api.user_illusts(uid)
                if 'illusts' in json_result:
                    target_illusts.extend(json_result.illusts[:PIXIV_LIMIT])
            except Exception as e:
                logger.error(f"画师 {uid} 抓取失败: {e}")
    else:
        # 2. 否则抓取推荐
        logger.info("🎨 正在抓取推荐榜单")
        try:
            json_result = api.illust_recommended(content_type="illust")
            if 'illusts' in json_result:
                target_illusts.extend(json_result.illusts[:PIXIV_LIMIT])
        except Exception as e:
            logger.error(f"推荐榜单抓取失败: {e}")

    # --- 处理图片 ---
    headers = {"Referer": "https://www.pixiv.net/"}
    for illust in target_illusts:
        pid = illust.id
        img_url = illust.image_urls.large
        tags = " ".join([t.name for t in illust.tags])
        caption = f"Pixiv ID: {pid}\nArtist: {illust.user.name}\nTags: #{tags.replace(' ', ' #')}"
        
        # 下载
        async with aiohttp.ClientSession() as session:
            async with session.get(img_url, headers=headers) as resp:
                if resp.status == 200:
                    img_bytes = await resp.read()
                    await process_image(BytesIO(img_bytes), pid, tags, caption, "pixiv")
        
        await asyncio.sleep(2)

async def fetch_yande():
    """Yande 抓取逻辑"""
    logger.info(f"🔍 正在检查 Yande (Limit: {YANDE_LIMIT})...")
    url = f"https://yande.re/post.json?limit={YANDE_LIMIT}&tags=order:random"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200: return
            posts = await resp.json()
            
            for post in posts:
                img_url = post.get('sample_url') or post.get('file_url')
                if not img_url: continue
                
                pid = post.get('id')
                tags = post.get('tags', '')
                caption = f"Yande ID: {pid}\nTags: #{tags.replace(' ', ' #')}"
                
                async with session.get(img_url) as img_resp:
                    if img_resp.status == 200:
                        img_bytes = await img_resp.read()
                        await process_image(BytesIO(img_bytes), pid, tags, caption, "yande")
                await asyncio.sleep(2)

# --- 6. 主循环 ---
async def main():
    logger.info("🚀 Bot 服务已启动...")
    while True:
        await fetch_yande()
        if HAS_PIXIV:
            await fetch_pixiv()
        
        logger.info("😴 休息 10 分钟...")
        await asyncio.sleep(600)

if __name__ == "__main__":
    asyncio.run(main())
