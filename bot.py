import os
import asyncio
import logging
import time
import json
from io import BytesIO
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile
from dotenv import load_dotenv

# 尝试导入 pixivpy3
try:
    from pixivpy3 import AppPixivAPI
    HAS_PIXIV_LIB = True
except ImportError:
    HAS_PIXIV_LIB = False

load_dotenv()

# --- 日志配置 ---
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 变量读取 ---
def get_env(key, default=None):
    val = os.getenv(key) or os.getenv(key.replace("_", " "))
    if val: return val.strip()
    return default

# ===========================
# 🟢 核心变量配置区
# ===========================
BOT_TOKEN = get_env("BOT_TOKEN")
CHANNEL_ID = get_env("CHANNEL_ID")

# Worker 相关 (用于云端记忆)
# 格式如: https://mtcacg.yourname.workers.dev
WORKER_URL = get_env("WORKER_URL") 

# Cloudflare D1
CF_ACCOUNT_ID = get_env("CLOUDFLARE_ACCOUNT_ID") or get_env("CF_ACCOUNT_ID")
CF_API_TOKEN = get_env("CLOUDFLARE_API_TOKEN") or get_env("CF_API_TOKEN")
D1_DB_ID = get_env("D1_DATABASE_ID")

# Yande 爬虫配置
YANDE_LIMIT = int(get_env("YANDE_LIMIT", 1))
YANDE_TAGS = get_env("YANDE_TAGS", "order:random")

# Pixiv 爬虫配置
PIXIV_PHPSESSID = get_env("PIXIV_PHPSESSID")       
PIXIV_REFRESH_TOKEN = get_env("PIXIV_REFRESH_TOKEN") 
PIXIV_ARTIST_IDS = get_env("PIXIV_ARTIST_IDS", "") 
PIXIV_LIMIT = int(get_env("PIXIV_LIMIT", 3))       

# ===========================
# 🚀 启动检查
# ===========================
if not all([BOT_TOKEN, CHANNEL_ID, CF_ACCOUNT_ID, CF_API_TOKEN, D1_DB_ID]):
    logger.error("❌ 致命错误：缺少核心环境变量 (BOT_TOKEN, CHANNEL_ID, CF_ACCOUNT_ID, CF_API_TOKEN, D1_DATABASE_ID)")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===========================
# 🧠 云端记忆模块 (新增)
# ===========================
sent_illust_ids = set() # 内存里的已发送ID集合

async def sync_history_from_cloud():
    """从 Worker 下载历史记录"""
    if not WORKER_URL: return
    global sent_illust_ids
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{WORKER_URL}/api/get_history") as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if text:
                        # 假设 ID 用逗号分隔
                        ids = text.split(',')
                        sent_illust_ids = set(ids)
                        logger.info(f"🧠 已同步云端记忆，共 {len(sent_illust_ids)} 条记录。")
    except Exception as e:
        logger.warning(f"⚠️ 同步历史记录失败: {e}")

async def push_history_to_cloud():
    """把最新的历史记录上传回 Worker"""
    if not WORKER_URL: return
    try:
        data = ",".join(sent_illust_ids)
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{WORKER_URL}/api/update_history", data=data) as resp:
                if resp.status == 200:
                    logger.info("☁️ 记忆已更新到云端。")
    except Exception as e:
        logger.warning(f"⚠️ 上传历史记录失败: {e}")

# ===========================
# 🛠️ 核心工具函数
# ===========================

async def save_to_d1(post_id, file_id, caption, tags, source):
    """把 TG FileID 写入 Cloudflare D1"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DB_ID}/query"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    
    sql = "INSERT OR IGNORE INTO images (id, file_name, caption, tags, created_at) VALUES (?, ?, ?, ?, ?)"
    final_tags = f"{tags} {source}".strip()
    params = [str(post_id), file_id, caption, final_tags, int(time.time())]
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={"sql": sql, "params": params}) as resp:
            if resp.status == 200:
                logger.info(f"💾 D1 写入成功: {post_id}")
            else:
                logger.error(f"❌ D1 写入失败: {await resp.text()}")

async def process_image(img_bytes, post_id, tags, caption, source):
    """统一处理流程：发TG -> 拿ID -> 存D1"""
    try:
        # 1. 包装图片
        tg_file = BufferedInputFile(img_bytes, filename=f"{source}.jpg")
        
        # 2. 发送到存储频道
        msg = await bot.send_photo(chat_id=int(CHANNEL_ID), photo=tg_file, caption=caption)
        
        # 3. 提取最高清图片的 FileID
        file_id = msg.photo[-1].file_id
        
        # 4. 存库
        await save_to_d1(post_id, file_id, caption, tags, source)
        logger.info(f"✅ [{source}] 收录完成: {post_id}")
        
    except Exception as e:
        logger.error(f"⚠️ [{source}] 处理失败: {e}")

# ===========================
# 🎮 功能 1: 手动转发监听
# ===========================
@dp.message(F.photo)
async def handle_manual_forward(message: Message):
    try:
        file_id = message.photo[-1].file_id
        caption = message.caption or "Forwarded Image"
        post_id = f"manual_{message.message_id}"
        tags = "manual forwarded"
        
        sent_msg = await bot.send_photo(chat_id=int(CHANNEL_ID), photo=file_id, caption=caption)
        final_file_id = sent_msg.photo[-1].file_id
        
        await save_to_d1(post_id, final_file_id, caption, tags, "manual")
        await message.reply("✅ 图片已成功收录！")
        
    except Exception as e:
        logger.error(f"手动转发处理出错: {e}")
        await message.reply("❌ 收录失败，请检查日志")

# ===========================
# 🕸️ 功能 2: Yande 爬虫
# ===========================
async def fetch_yande():
    logger.info(f"🔍 检查 Yande ({YANDE_TAGS})...")
    url = f"https://yande.re/post.json?limit={YANDE_LIMIT}&tags={YANDE_TAGS}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200: return
                posts = await resp.json()
                
                for post in posts:
                    img_url = post.get('sample_url') or post.get('file_url')
                    if not img_url: continue
                    
                    pid = f"yande_{post['id']}"
                    # Yande 一般不需要像 Pixiv 那样严格去重，因为 Random 是随机的
                    # 如果需要去重，也可以在这里加判断逻辑
                    
                    caption = f"Yande: {post['id']}
Tags: #{post.get('tags','').replace(' ', ' #')}"
                    
                    async with session.get(img_url) as r:
                        if r.status == 200:
                            await process_image(await r.read(), pid, post.get('tags',''), caption, "yande")
                    
                    await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Yande 爬虫出错: {e}")

# ===========================
# 🎨 功能 3: Pixiv 爬虫 (带去重)
# ===========================
async def fetch_pixiv_by_cookie(artist_ids):
    """【Cookie 模式】模拟浏览器 API，不需要 Token"""
    logger.info("🍪 使用 Cookie 模式爬取 Pixiv...")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Cookie": f"PHPSESSID={PIXIV_PHPSESSID}",
        "Referer": "https://www.pixiv.net/"
    }
    
    has_new_images = False # 标记本轮是否有新图
    
    async with aiohttp.ClientSession(headers=headers) as session:
        for uid in artist_ids:
            try:
                # 1. 获取画师作品列表
                async with session.get(f"https://www.pixiv.net/ajax/user/{uid}/profile/all") as r:
                    data = await r.json()
                    if data['error']: 
                        logger.warning(f"Pixiv Cookie 失效或画师ID错误 (UID {uid})")
                        continue
                    
                    # 提取最新的 N 个 ID
                    ids = sorted(list(data['body']['illusts'].keys()), key=int, reverse=True)[:PIXIV_LIMIT]
                
                # 2. 遍历详情并下载
                for pid in ids:
                    # --- 去重检查 ---
                    if str(pid) in sent_illust_ids:
                        logger.info(f"⏭️ Pixiv {pid} 以前发过，跳过。")
                        continue
                        
                    async with session.get(f"https://www.pixiv.net/ajax/illust/{pid}") as r:
                        info = await r.json()
                        body = info['body']
                        title = body['illustTitle']
                        user = body['userName']
                        tags = " ".join([t['tag'] for t in body['tags']['tags']])
                        img_url = body['urls']['original']
                        
                        caption = f"Pixiv: {title}
Artist: {user}
Tags: #{tags.replace(' ', ' #')}"
                        post_id = f"pixiv_{pid}"
                        
                        # 下载原图
                        async with session.get(img_url) as img_r:
                            if img_r.status == 200:
                                await process_image(await img_r.read(), post_id, tags, caption, "pixiv")
                                
                                # --- 标记为已发送 ---
                                sent_illust_ids.add(str(pid))
                                has_new_images = True
                        
                        await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Pixiv Cookie 爬取失败 (UID {uid}): {e}")
    
    # 如果有新图，更新云端记忆
    if has_new_images:
        await push_history_to_cloud()

async def fetch_pixiv():
    # 1. Token 模式 (略，逻辑类似，但目前代码主要用 Cookie)
    if HAS_PIXIV_LIB and PIXIV_REFRESH_TOKEN:
        try:
            logger.info("🔑 使用 Token 模式爬取 Pixiv...")
            api = AppPixivAPI()
            api.auth(refresh_token=PIXIV_REFRESH_TOKEN)
            # (如果需要 Token 模式也去重，需要在这里加同样的逻辑)
        except: pass
    
    # 2. 回退到 Cookie 模式
    if PIXIV_PHPSESSID and PIXIV_ARTIST_IDS:
        uids = [x.strip() for x in PIXIV_ARTIST_IDS.split(',') if x.strip()]
        await fetch_pixiv_by_cookie(uids)

# ===========================
# ⏱️ 调度器 & 主程序
# ===========================
async def scheduler():
    # 启动时先同步一次记忆
    await sync_history_from_cloud()
    
    while True:
        await fetch_yande()
        await fetch_pixiv()
        logger.info("😴 休息 10 分钟...")
        await asyncio.sleep(600)

async def main():
    logger.info("🚀 终极图库 Bot (TG图床版) 已启动...")
    await asyncio.gather(dp.start_polling(bot), scheduler())

if __name__ == "__main__":
    asyncio.run(main())
