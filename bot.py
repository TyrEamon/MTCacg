import os
import asyncio
import logging
import json
import random
from io import BytesIO
import aiohttp
import boto3
from aiogram import Bot
from aiogram.types import BufferedInputFile
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# --- 配置日志 ---
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 获取并检查环境变量 ---
try:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
    
    # Cloudflare 配置
    CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID") or os.getenv("R2_ACCOUNT_ID")
    CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN") # 用于 D1 操作
    
    # R2 配置
    R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY_ID")
    R2_SECRET_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
    R2_BUCKET = os.getenv("R2_BUCKET_NAME")
    R2_ENDPOINT = f"https://{CF_ACCOUNT_ID}.r2.cloudflarestorage.com"
    
    # D1 配置
    D1_DB_ID = os.getenv("D1_DATABASE_ID")
    
    if not all([BOT_TOKEN, CHANNEL_ID, CF_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET, D1_DB_ID, CF_API_TOKEN]):
        logger.error("❌ 缺少必要的环境变量，请检查 Leaflow 配置！")
        # 此时不退出，避免容器无限重启，但后续功能会失败
except Exception as e:
    logger.error(f"❌ 环境变量配置错误: {e}")
    exit(1)

# --- 初始化客户端 ---
bot = Bot(token=BOT_TOKEN)

# R2 客户端 (boto3)
s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY
)

# --- 核心功能函数 ---

async def upload_to_r2(file_data: BytesIO, filename: str):
    """上传文件到 Cloudflare R2"""
    try:
        file_data.seek(0) # 重置指针到开头
        s3_client.upload_fileobj(
            file_data, 
            R2_BUCKET, 
            filename,
            ExtraArgs={'ContentType': 'image/jpeg'}
        )
        logger.info(f"✅ 图片已上传到 R2: {filename}")
        return True
    except Exception as e:
        logger.error(f"❌ R2 上传失败: {e}")
        return False

async def save_to_d1(post_id, file_name, caption, tags):
    """通过 API 写入 Cloudflare D1"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DB_ID}/query"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # 构建 SQL (假设表名叫 images)
    sql = "INSERT INTO images (id, file_name, caption, tags, created_at) VALUES (?, ?, ?, ?, ?)"
    params = [str(post_id), file_name, caption, tags, int(asyncio.get_event_loop().time())]
    
    payload = {
        "sql": sql,
        "params": params
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                logger.info(f"✅ 数据已写入 D1: {post_id}")
            else:
                text = await resp.text()
                logger.error(f"❌ D1 写入失败: {text}")

async def fetch_and_post():
    """主逻辑：抓取 -> 上传 -> 发送"""
    try:
        # 1. 抓取图片源 (以 Yande 为例，你可以改)
        api_url = "https://yande.re/post.json?limit=1&tags=order:random"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                posts = await resp.json()
                if not posts: return
                
                post = posts[0]
                image_url = post.get('sample_url') or post.get('file_url')
                post_id = post.get('id')
                tags = post.get('tags', '')
                file_name = f"{post_id}.jpg"

                logger.info(f"📥 正在下载图片: {post_id}...")

                # 2. 下载图片到内存
                async with session.get(image_url) as img_resp:
                    if img_resp.status != 200: return
                    
                    # 读取二进制数据
                    img_bytes = await img_resp.read()
                    img_buffer = BytesIO(img_bytes)

        # 3. 发送到 Telegram (修复了 validation error)
        caption = f"ID: {post_id}\nTags: #{tags.replace(' ', ' #')}"
        
        # 关键修正：使用 BufferedInputFile 并指定 filename
        tg_file = BufferedInputFile(img_buffer.getvalue(), filename=file_name)
        
        await bot.send_photo(chat_id=CHANNEL_ID, photo=tg_file, caption=caption)
        logger.info("✅ 已发送到 Telegram")

        # 4. 上传到 R2
        await asyncio.to_thread(upload_to_r2, img_buffer, file_name)

        # 5. 写入 D1
        await save_to_d1(post_id, file_name, caption, tags)

    except Exception as e:
        logger.error(f"⚠️ 发生错误: {e}")

async def main():
    logger.info("🚀 Bot 已启动...")
    while True:
        await fetch_and_post()
        # 每 60 秒运行一次，可自行调整
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
