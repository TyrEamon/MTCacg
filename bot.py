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
    val = os.getenv(key) or os.getenv(key.replace("_", " "))
    if val: return val.strip()
    return default

# --- 2. 核心变量配置 ---
BOT_TOKEN = get_env("BOT_TOKEN")
CHANNEL_ID = get_env("CHANNEL_ID")

# Cloudflare 相关
CF_ACCOUNT_ID = get_env("CLOUDFLARE_ACCOUNT_ID") or get_env("CF_ACCOUNT_ID")
CF_API_TOKEN = get_env("CLOUDFLARE_API_TOKEN") or get_env("CF_API_TOKEN")
D1_DB_ID = get_env("D1_DATABASE_ID")

# R2 相关
R2_ACCESS_KEY = get_env("R2_ACCESS_KEY_ID")
R2_SECRET_KEY = get_env("R2_SECRET_ACCESS_KEY")
R2_BUCKET = get_env("R2_BUCKET_NAME")

# Pixiv 相关
PIXIV_PHPSESSID = get_env("PIXIV_PHPSESSID")
PIXIV_REFRESH_TOKEN = get_env("PIXIV_REFRESH_TOKEN")
PIXIV_ARTIST_IDS = get_env("PIXIV_ARTIST_IDS", "")
PIXIV_LIMIT = int(get_env("PIXIV_LIMIT", 3))

# Yande 相关
YANDE_LIMIT = int(get_env("YANDE_LIMIT", 1))
YANDE_TAGS = get_env("YANDE_TAGS", "order:random") # 默认 random, 支持 order:score

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
        
        # 修复点：确保指针在开头
        img_buffer.seek(0)
        file_bytes = img_buffer.read()
        img_buffer.seek(0) # 重置给 R2 用
        
        # 1. 发送 TG
        tg_file = BufferedInputFile(file_bytes, filename=file_name)
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
    """Pixiv 抓取逻辑 (PHPSESSID 补丁版)"""
    if not HAS_PIXIV: return

    logger.info("🔍 正在检查 Pixiv...")
    api = AppPixivAPI()
    
    # --- 登录逻辑 (增强版) ---
    try:
        if PIXIV_REFRESH_TOKEN:
            api.auth(refresh_token=PIXIV_REFRESH_TOKEN)
            logger.info("✅ Pixiv: Token 登录成功")
        elif PIXIV_PHPSESSID:
            # 🔴 PHPSESSID 补丁: 强行注入 Cookie
            # 注意: pixivpy3 原生不支持这样，我们这里只是尝试让它带上头
            # 如果这步失败，说明 pixivpy3 彻底不支持纯 cookie，必须换库
            api.requests_kwargs.update({
                'headers': {
                    'User-Agent': 'PixivAndroidApp/5.0.155',
                    'Cookie': f'PHPSESSID={PIXIV_PHPSESSID};'
                }
            })
            logger.info("⚠️ Pixiv: 尝试使用 PHPSESSID 模式 (可能不稳定)")
        else:
            return
    except Exception as e:
        logger.error(f"Pixiv 登录异常: {e}")
        return

    # --- 抓取逻辑 ---
    target_illusts = []

    # 1. 抓取指定画师
    if PIXIV_ARTIST_IDS:
        artist_ids = [x.strip() for x in PIXIV_ARTIST_IDS.split(',') if x.strip()]
        logger.info(f"🎨 正在抓取指定画师: {artist_ids}")
        for uid in artist_ids:
            try:
                # 尝试抓取
                json_result = api.user_illusts(uid)
                if json_result and 'illusts' in json_result:
                    target_illusts.extend(json_result.illusts[:PIXIV_LIMIT])
                else:
                    logger.warning(f"画师 {uid} 未返回数据 (可能是 Cookie 失效)")
            except Exception as e:
                logger.error(f"画师 {uid} 抓取失败: {e}")
    else:
        # 2. 抓取推荐
        try:
            json_result = api.illust_recommended(content_type="illust")
            if json_result and 'illusts' in json_result:
                target_illusts.extend(json_result.illusts[:PIXIV_LIMIT])
        except Exception as e:
            logger.error(f"推荐榜单抓取失败: {e}")

    # --- 处理图片 ---
    # Pixiv 图片有防盗链，必须带 Referer
    headers = {"Referer": "https://app-api.pixiv.net/"} 
    
    for illust in target_illusts:
        pid = illust.id
        # 优先拿大图
        img_url = illust.image_urls.large if illust.image_urls.large else illust.image_urls.medium
        
        tags = " ".join([t.name for t in illust.tags])
        caption = f"Pixiv ID: {pid}\nArtist: {illust.user.name}\nTags: #{tags.replace(' ', ' #')}"
        
        # 下载 (注意：这里不能用 pixivpy 下载，得用 aiohttp 带 header 下载)
        async with aiohttp.ClientSession() as session:
            async with session.get(img_url, headers=headers) as resp:
                if resp.status == 200:
                    img_bytes = await resp.read()
                    await process_image(BytesIO(img_bytes), pid, tags, caption, "pixiv")
                else:
                    logger.warning(f"Pixiv 图片下载失败 {resp.status}: {img_url}")
        
        await asyncio.sleep(2)

async def fetch_yande():
    """Yande 抓取逻辑 (支持自定义 Tags)"""
    logger.info(f"🔍 正在检查 Yande (Tags: {YANDE_TAGS})...")
    url = f"https://yande.re/post.json?limit={YANDE_LIMIT}&tags={YANDE_TAGS}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200: return
                posts = await resp.json()
                
                if not posts:
                    logger.info("⚠️ Yande 无数据")
                    return

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
    except Exception as e:
        logger.error(f"Yande 出错: {e}")

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
