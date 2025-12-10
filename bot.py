import asyncio
import json
import os
import time
import logging
from io import BytesIO

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InputMediaPhoto
from pixivpy3 import AppPixivAPI
import aiohttp
import boto3

# === 环境变量配置 ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID")) 

# Cloudflare D1 & R2
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CF_API_TOKEN") 
D1_DATABASE_ID = os.getenv("D1_DATABASE_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_ENDPOINT_URL = f"https://{CF_ACCOUNT_ID}.r2.cloudflarestorage.com"

# 爬虫控制
PIXIV_REFRESH_TOKEN = os.getenv("PIXIV_REFRESH_TOKEN")
PIXIV_ARTIST_IDS = os.getenv("PIXIV_ARTIST_IDS", "") 
PIXIV_LIMIT = int(os.getenv("PIXIV_LIMIT", "3"))

YANDE_LIMIT = int(os.getenv("YANDE_LIMIT", "10"))
YANDE_TAGS = os.getenv("YANDE_TAGS", "") 

# === 初始化 ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
pixiv_api = AppPixivAPI()

s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY
)

# === 工具函数 ===

async def upload_to_channel(file_source, caption=""):
    """上传图片到 Telegram (支持 URL 和 BytesIO)"""
    try:
        # 如果是 BytesIO (内存文件)
        if hasattr(file_source, 'read'):
            msg = await bot.send_photo(chat_id=CHANNEL_ID, photo=file_source, caption=caption)
            return msg.photo[-1].file_id
        
        # 如果是 URL (仅当确定文件很小且直链可访问时才用，一般建议用上面的流式上传)
        elif isinstance(file_source, str) and file_source.startswith("http"):
            msg = await bot.send_photo(chat_id=CHANNEL_ID, photo=file_source, caption=caption)
            return msg.photo[-1].file_id
            
    except Exception as e:
        logging.error(f"Telegram upload failed: {e}")
        return None

async def check_if_exists_in_d1(post_id):
    """查重"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DATABASE_ID}/query"
    sql = "SELECT 1 FROM posts WHERE id = ? LIMIT 1"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"}, json={"sql": sql, "params": [post_id]}) as resp:
            data = await resp.json()
            if data.get('success') and data.get('result') and data['result'][0].get('results'):
                return True
            return False

async def write_to_d1(post_data):
    """写入 D1"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DATABASE_ID}/query"
    sql = """
    INSERT INTO posts (id, source, title, author, tags, images, r2_key, timestamp) 
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO NOTHING;
    """
    params = [
        post_data['id'], post_data['source'], post_data['title'], post_data['author'],
        json.dumps(post_data['tags']), json.dumps(post_data['images']), 
        post_data['r2_key'], post_data['timestamp']
    ]
    async with aiohttp.ClientSession() as session:
        await session.post(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"}, json={"sql": sql, "params": params})

def write_to_r2(key, data):
    """写入 R2"""
    try:
        s3_client.put_object(Bucket=R2_BUCKET_NAME, Key=key, Body=json.dumps(data, ensure_ascii=False), ContentType='application/json')
    except Exception as e:
        logging.error(f"R2 Write Error: {e}")

# === 业务逻辑 1: 手动转发 (相册支持) ===
album_buffer = {}
@dp.message(F.media_group_id)
async def handle_album(message: types.Message):
    mg_id = message.media_group_id
    if mg_id not in album_buffer:
        album_buffer[mg_id] = []
        asyncio.create_task(process_album_later(mg_id, message))
    album_buffer[mg_id].append(message)

async def process_album_later(mg_id, first_msg):
    await asyncio.sleep(4)
    messages = album_buffer.pop(mg_id, [])
    if not messages: return
    caption = next((m.caption for m in messages if m.caption), "无题")
    title = caption.split('\n')[0][:50]
    
    # 转发相册
    media_group = [InputMediaPhoto(media=m.photo[-1].file_id) for m in messages if m.photo]
    if not media_group: return
    sent_msgs = await bot.send_media_group(chat_id=CHANNEL_ID, media=media_group)
    final_file_ids = [m.photo[-1].file_id for m in sent_msgs]
    
    post_id = f"manual_{first_msg.message_id}"
    post_data = {"id": post_id, "source": "manual", "title": title, "author": "Me", "tags": ["manual"], "images": final_file_ids, "r2_key": f"posts/{post_id}.json", "timestamp": int(time.time())}
    write_to_r2(post_data['r2_key'], post_data)
    await write_to_d1(post_data)
    await first_msg.reply("✅")

@dp.message(F.photo)
async def handle_single_photo(message: types.Message):
    if message.media_group_id: return
    msg = await message.forward(CHANNEL_ID)
    file_id = msg.photo[-1].file_id
    post_id = f"manual_{message.message_id}"
    post_data = {"id": post_id, "source": "manual", "title": (message.caption or "无题")[:50], "author": "Me", "tags": ["manual"], "images": [file_id], "r2_key": f"posts/{post_id}.json", "timestamp": int(time.time())}
    write_to_r2(post_data['r2_key'], post_data)
    await write_to_d1(post_data)
    await message.reply("✅")

# === 业务逻辑 2: Yande (含大图下载补丁) ===
async def task_yande():
    while True:
        try:
            limit = int(os.getenv("YANDE_LIMIT", "10"))
            tags = os.getenv("YANDE_TAGS", "")
            logging.info(f"Yande: Limit={limit}, Tags={tags or 'Latest'}")
            
            url = f"https://yande.re/post.json?limit={limit}"
            if tags: url += f"&tags={tags}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logging.error(f"Yande API: {resp.status}")
                        await asyncio.sleep(600)
                        continue
                    posts = await resp.json()

            for post in posts:
                post_id = f"yande_{post['id']}"
                if await check_if_exists_in_d1(post_id):
                    if limit < 20: break 
                    else: continue
                
                # 优先下载 jpeg_url (Sample图)，通常小于10MB
                target_url = post.get('jpeg_url') or post.get('file_url')
                if not target_url: continue
                
                logging.info(f"Downloading Yande #{post['id']}...")
                
                # 下载到内存再上传 (解决20MB限制问题)
                async with aiohttp.ClientSession() as session:
                    async with session.get(target_url) as img_resp:
                        if img_resp.status == 200:
                            img_data = BytesIO(await img_resp.read())
                            caption = f"Yande #{post['id']} {post.get('rating','')}"
                            
                            file_id = await upload_to_channel(img_data, caption=caption)
                            
                            if file_id:
                                post_data = {"id": post_id, "source": "yande", "title": f"Yande #{post['id']}", "author": post.get('author','Unknown'), "tags": post.get('tags','').split(' '), "images": [file_id], "r2_key": f"posts/{post_id}.json", "timestamp": post.get('created_at', int(time.time()))}
                                write_to_r2(post_data['r2_key'], post_data)
                                await write_to_d1(post_data)
                                logging.info(f"Saved Yande #{post['id']}")
                        
                        # 安全延迟 (重要)
                        await asyncio.sleep(3)
                    
        except Exception as e:
            logging.error(f"Yande Error: {e}")
        
        await asyncio.sleep(600) # 10分钟检查一次

# === 业务逻辑 3: Pixiv (含 Limit 控制) ===
async def task_pixiv():
    while True:
        try:
            limit = int(os.getenv("PIXIV_LIMIT", "3"))
            artist_str = os.getenv("PIXIV_ARTIST_IDS", "")
            if not artist_str or not PIXIV_REFRESH_TOKEN:
                await asyncio.sleep(3600)
                continue
            
            logging.info(f"Pixiv: Limit={limit}")
            if not pixiv_api.access_token: pixiv_api.auth(refresh_token=PIXIV_REFRESH_TOKEN)
            artist_ids = [int(x) for x in artist_str.split(',') if x.strip().isdigit()]
            
            for uid in artist_ids:
                try:
                    res = pixiv_api.user_illusts(uid)
                    if not res.illusts: continue
                    
                    for illust in res.illusts[:limit]:
                        post_id = f"pixiv_{illust.id}"
                        if await check_if_exists_in_d1(post_id): continue
                        
                        logging.info(f"DL Pixiv {illust.id}")
                        headers = {"Referer": "https://app-api.pixiv.net/"}
                        async with aiohttp.ClientSession() as session:
                            async with session.get(illust.image_urls.large, headers=headers) as resp:
                                if resp.status == 200:
                                    img_data = BytesIO(await resp.read())
                                    file_id = await upload_to_channel(img_data, caption=f"Pixiv {illust.title}")
                                    if file_id:
                                        post_data = {"id": post_id, "source": "pixiv", "title": illust.title, "author": illust.user.name, "tags": [t.name for t in illust.tags], "images": [file_id], "r2_key": f"posts/{post_id}.json", "timestamp": int(time.time())}
                                        write_to_r2(post_data['r2_key'], post_data)
                                        await write_to_d1(post_data)
                        
                        # 安全延迟 (重要)
                        await asyncio.sleep(5)
                        
                except Exception as e: logging.error(f"Pixiv Artist {uid} error: {e}")
        except Exception as e:
            logging.error(f"Pixiv Task Error: {e}")
        
        await asyncio.sleep(3600 * 4)

async def main():
    asyncio.create_task(task_yande())
    asyncio.create_task(task_pixiv())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
