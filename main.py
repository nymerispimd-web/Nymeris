# --- 1. HEALTH CHECK SERVER ---
import discord
import io
import os
import json
import threading
import re
import asyncio
from PIL import Image
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_health_check():
    try:
        server = ThreadingHTTPServer(('0.0.0.0', 7860), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Health check server error: {e}")

threading.Thread(target=run_health_check, daemon=True).start()

# --- 2. CONFIGURATION & PERSISTENCE ---
TOKEN = os.getenv('DISCORD_TOKEN')
STORAGE_DIR = "/data/" if os.path.exists("/data/") else "/tmp/"
PREFS_FILE = os.path.join(STORAGE_DIR, "user_prefs.json")

# List of channels to track numbers in
TRACKED_CHANNELS = [1518305844591202494, 1518298488818106429]

def load_prefs():
    if os.path.exists(PREFS_FILE):
        try:
            with open(PREFS_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_pref(user_id, device):
    try:
        prefs = load_prefs()
        prefs[str(user_id)] = device
        with open(PREFS_FILE, "w") as f:
            json.dump(prefs, f)
    except Exception as e:
        print(f"Failed to save user preference: {e}")

# --- 3. BOT INITIALIZATION ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# --- 4. UI VIEWS ---

class ConfirmDeleteView(discord.ui.View):
    def __init__(self, original_message):
        super().__init__(timeout=60)
        self.original_message = original_message

    @discord.ui.button(label="Yes, Delete Post", style=discord.ButtonStyle.danger)
    async def confirm_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.original_message.delete()
            await interaction.response.edit_message(content="✅ Post deleted.", view=None)
        except:
            await interaction.response.send_message("I couldn't delete that message.", ephemeral=True)

class DeleteButtonView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    @discord.ui.button(label="Delete Post", style=discord.ButtonStyle.secondary)
    async def delete_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.owner_id:
            view = ConfirmDeleteView(original_message=interaction.message)
            await interaction.response.send_message(content="⚠️ **Delete this post?**", view=view, ephemeral=True)
        else:
            await interaction.response.send_message("Only the person who triggered this can delete it!", ephemeral=True)

class DeviceSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    async def process_and_save(self, interaction, device):
        save_pref(interaction.user.id, device)
        device_name = "iOS" if device == "ios" else "Android"
        await interaction.response.send_message(
            content=f"✅ **Preference Saved!** I will now use **{device_name}** crops for your photos.",
            ephemeral=True
        )
        try:
            await interaction.message.delete()
        except:
            pass

    @discord.ui.button(label="Android", style=discord.ButtonStyle.primary)
    async def android_button(self, interaction, button):
        await self.process_and_save(interaction, "android")

    @discord.ui.button(label="iOS (iPhone)", style=discord.ButtonStyle.secondary)
    async def ios_button(self, interaction, button):
        await self.process_and_save(interaction, "ios")

# --- 5. CORE CROP LOGIC ---

async def perform_crop(message, attachments, clean_text, offset):
    crop_happened = False
    for attachment in attachments:
        try:
            img_bytes = await attachment.read()
            img = Image.open(io.BytesIO(img_bytes))
            width, height = img.size
            
            if height <= width:
                continue

            box = (0, offset, width, min(offset + width, height))
            cropped_img = img.crop(box)
            
            with io.BytesIO() as image_binary:
                cropped_img.save(image_binary, 'PNG')
                image_binary.seek(0)
                
                file = discord.File(fp=image_binary, filename="cropped.png")
                view = DeleteButtonView(owner_id=message.author.id)
                content = f"{message.author.mention} {clean_text}"
                
                await message.channel.send(content=content, file=file, view=view)
                crop_happened = True
                try: 
                    await message.delete()
                except: 
                    pass
                    
        except Exception as e:
            print(f"Error during cropping/sending: {e}")
    return crop_happened

# --- 6. EVENTS ---

@client.event
async def on_ready():
    print(f'Logged in as {client.user.name}. Storage path: {STORAGE_DIR}')

@client.event
async def on_message(message):
    if message.author.bot and message.author != client.user:
        return

    missed_message = None
    
    # --- SEQUENCE CHECK LOGIC ---
    if message.channel.id in TRACKED_CHANNELS:
        match = re.search(r'#(\d+)', message.content)
        if match:
            current_num = int(match.group(1))
            actual_user_id = message.author.id
            actual_user_mention = message.author.mention
            
            if message.author == client.user and message.mentions:
                actual_user_id = message.mentions[0].id
                actual_user_mention = message.mentions[0].mention
            
            last_num = 0
            last_user_id = None
            
            # Dynamically fetches history for the *current* channel only
            async for past_msg in message.channel.history(limit=10, before=message):
                if past_msg.author.id == 1463361569424543898:
                    continue  # Ignore this specific user
                    
                past_match = re.search(r'#(\d+)', past_msg.content)
                if past_match:
                    last_num = int(past_match.group(1))
                    last_user_id = past_msg.author.id
                    if past_msg.author == client.user and past_msg.mentions:
                        last_user_id = past_msg.mentions[0].id
                    break
            
            if last_num != 0 and current_num > last_num + 1:
                gap = range(last_num + 1, current_num)
                if len(gap) > 30:
                    missed_nums = f"{last_num + 1} through {current_num - 1}"
                else:
                    missed_nums = ", #".join([str(i) for i in gap])
                
                if last_user_id and str(last_user_id) != str(actual_user_id):
                    tags = f"<@{last_user_id}> {actual_user_mention}"
                else:
                    tags = actual_user_mention
                    
                missed_message = f"{tags} ⚠️ Party number #{missed_nums} is missing."

    if message.author == client.user:
        if missed_message:
            await message.channel.send(missed_message, allowed_mentions=discord.AllowedMentions.all())
        return

    # --- IMAGE & CROP LOGIC ---
    image_attachments = [a for a in message.attachments if a.filename.lower().endswith(('png', 'jpg', 'jpeg', 'webp'))]
    clean_text = message.content
    for mention in message.mentions:
        if mention == client.user:
            clean_text = clean_text.replace(mention.mention, "")
    clean_text = clean_text.strip()

    is_reply = message.reference is not None
    if client.user.mentioned_in(message) and not is_reply:
        if "reset" in clean_text.lower():
            prefs = load_prefs()
            user_id_str = str(message.author.id)
            if user_id_str in prefs:
                del prefs[user_id_str]
                with open(PREFS_FILE, "w") as f:
                    json.dump(prefs, f)
            await message.channel.send("✅ Preference cleared.", delete_after=5)
            try: await message.delete()
            except: pass
            return

        if not image_attachments:
            view = DeviceSelectView()
            await message.channel.send(
                f"{message.author.mention}, would you like to use Android or iOS crop settings?",
                view=view
            )
            try: await message.delete()
            except: pass
            return

    crop_performed = False
    if image_attachments:
        prefs = load_prefs()
        user_id_str = str(message.author.id)
        ios_users = [730138298621886544, 1454173039942963333, 1489206924045189140]
        default_device = "ios" if message.author.id in ios_users else "android"
        device = prefs.get(user_id_str, default_device)
        offset = 185 if device == "ios" else 110
        crop_performed = await perform_crop(message, image_attachments, clean_text, offset)

    if missed_message:
        if crop_performed:
            await asyncio.sleep(2) 
        await message.channel.send(missed_message, allowed_mentions=discord.AllowedMentions.all())

# --- 7. START ---
if TOKEN:
    client.run(TOKEN)
else:
    print("FATAL ERROR: DISCORD_TOKEN is missing.")
