import discord
from discord.ext import commands, tasks
import requests
import asyncio
import json
from dotenv import load_dotenv
import os

# 載入 .env 檔案
load_dotenv()

# Bot 設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# 從 .env 讀取設定
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
DISCORD_CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID'))

# 其他設定
USER_IDS = []  # 追蹤的 Roblox 玩家 ID 清單
USER_NAMES = {}  # {user_id: username} 儲存玩家名稱
CHECK_INTERVAL = 30  # 檢查間隔 (秒)，30 秒以實現立即通知
BATCH_SIZE = 50  # 每批檢查的最大玩家數，遵守 API 速率限制

# 儲存每個玩家的上一次狀態和通知標記
last_statuses = {}  # {user_id: is_in_game}，是否在遊戲中 (userPresenceType == 2)
notified_online = {}  # {user_id: bool} 記錄是否已發送「上線」通知
notified_offline = {}  # {user_id: bool} 記錄是否已發送「下線」通知

def get_user_id_from_username(username):
    """使用 Roblox API 將玩家名稱轉為 ID"""
    url = "https://users.roblox.com/v1/usernames/users"
    headers = {"Content-Type": "application/json"}
    data = {"usernames": [username], "excludeBannedUsers": True}
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            data = response.json()
            if data["data"] and len(data["data"]) > 0:
                return data["data"][0]["id"]
            return None
        return None
    except Exception as e:
        print(f"API 錯誤 (獲取 ID): {e}")
        return None

def check_roblox_online(user_ids):
    """使用 Roblox API 檢查多個玩家是否在線"""
    url = "https://presence.roblox.com/v1/presence/users"
    headers = {"Content-Type": "application/json"}
    data = {"userIds": user_ids}
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return response.json()["userPresences"]
        return []
    except Exception as e:
        print(f"API 錯誤 (檢查狀態): {e}")
        return []

async def check_status_batch(channel, user_ids, display_status=False):
    """分批檢查玩家狀態並處理通知，可選擇顯示狀態"""
    global last_statuses, notified_online, notified_offline
    if not user_ids:
        return
    
    # 分批處理，遵守 API 限制
    for i in range(0, len(user_ids), BATCH_SIZE):
        batch = user_ids[i:i + BATCH_SIZE]
        presences = check_roblox_online(batch)
        
        for presence in presences:
            user_id = presence["userId"]
            current_status = presence["userPresenceType"] == 2  # 2 = 在遊戲中
            username = USER_NAMES.get(user_id, str(user_id))
            profile_url = f"https://www.roblox.com/users/{user_id}/profile"
            
            # 檢查上線：玩家進入遊戲且未發送過上線通知
            if current_status and not notified_online.get(user_id, False):
                await channel.send(f"{username} 上線")
                notified_online[user_id] = True
                notified_offline[user_id] = False  # 重置下線標記
            
            # 檢查下線：玩家從遊戲中離開且上次在遊戲中且未發送過下線通知
            elif not current_status and user_id in last_statuses and last_statuses[user_id] and not notified_offline.get(user_id, False):
                await channel.send(f"{username} 下線")
                notified_offline[user_id] = True
                notified_online[user_id] = False  # 重置上線標記
            
            # 如果需要顯示狀態（例如 !check 觸發）
            if display_status:
                status = "上線" if current_status else "下線"
                await channel.send(f"玩家 {username} 目前狀態：{status}，個人檔案：{profile_url}")
            
            last_statuses[user_id] = current_status

@bot.event
async def on_ready():
    print(f'{bot.user} 已上線！開始追蹤 Roblox 玩家。')

@tasks.loop(seconds=CHECK_INTERVAL)
async def status_check():
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if not channel or not USER_IDS:
        return
    
    await check_status_batch(channel, USER_IDS, display_status=False)

@bot.command(name='helpme')
async def helpme(ctx):
    help_message = """
    **Roblox 追蹤機器人指令**
    !helpme - 顯示所有可用指令
    !check - 啟動或停止自動檢查並通知所有追蹤玩家的狀態，並顯示個人檔案連結
    !status - 顯示所有追蹤玩家的最後已知狀態及通知歷史
    !adduser <username> - 新增一個 Roblox 玩家名稱到追蹤清單
    !removeuser <username> - 移除一個 Roblox 玩家名稱從追蹤清單
    """
    await ctx.send(help_message)

@bot.command(name='check')
async def check(ctx):
    global USER_IDS
    if not USER_IDS:
        await ctx.send("目前沒有追蹤任何玩家，請使用 !adduser 新增。")
        return
    
    channel = ctx.channel
    if status_check.is_running():
        status_check.stop()
        await ctx.send("已停止自動檢查。")
    else:
        await ctx.send("開始自動檢查並通知玩家狀態...")
        await check_status_batch(channel, USER_IDS, display_status=True)
        status_check.start()

@bot.command(name='status')
async def status(ctx):
    if not last_statuses:
        await ctx.send("尚無任何狀態資料，請使用 !check 或等待自動檢查。")
        return
    
    for user_id, is_in_game in last_statuses.items():
        username = USER_NAMES.get(user_id, str(user_id))
        status = "上線" if is_in_game else "下線"
        online_notified = "是" if notified_online.get(user_id, False) else "否"
        offline_notified = "是" if notified_offline.get(user_id, False) else "否"
        await ctx.send(f"玩家 {username} 最後已知狀態：{status}，曾發送上線通知：{online_notified}，曾發送下線通知：{offline_notified}")

@bot.command(name='adduser')
async def adduser(ctx, username: str):
    global notified_online, notified_offline, USER_NAMES
    # 將玩家名稱轉為 ID
    user_id = get_user_id_from_username(username)
    if not user_id:
        await ctx.send(f"找不到玩家 {username}，請確認名稱是否正確。")
        return
    
    if user_id in USER_IDS:
        await ctx.send(f"玩家 {username} (ID: {user_id}) 已在追蹤清單中！")
        return
    
    USER_IDS.append(user_id)
    USER_NAMES[user_id] = username
    notified_online[user_id] = False  # 初始化上線通知標記
    notified_offline[user_id] = False  # 初始化下線通知標記
    await ctx.send(f"已新增玩家 {username} (ID: {user_id}) 到追蹤清單！")

@bot.command(name='removeuser')
async def removeuser(ctx, username: str):
    global USER_IDS, USER_NAMES, last_statuses, notified_online, notified_offline
    # 將玩家名稱轉為 ID
    user_id = get_user_id_from_username(username)
    if not user_id:
        await ctx.send(f"找不到玩家 {username}，請確認名稱是否正確。")
        return
    
    if user_id not in USER_IDS:
        await ctx.send(f"玩家 {username} (ID: {user_id}) 不在追蹤清單中！")
        return
    
    # 移除玩家的所有相關資料
    USER_IDS.remove(user_id)
    USER_NAMES.pop(user_id, None)
    last_statuses.pop(user_id, None)
    notified_online.pop(user_id, None)
    notified_offline.pop(user_id, None)
    await ctx.send(f"已從追蹤清單移除玩家 {username} (ID: {user_id})！")
    
    # 如果追蹤清單為空，停止自動檢查
    if not USER_IDS and status_check.is_running():
        status_check.stop()

# 運行 bot
bot.run(DISCORD_TOKEN)
