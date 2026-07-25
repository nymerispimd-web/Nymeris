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

class EditPostModal(discord.ui.Modal, title="Edit Post"):
    new_text = discord.ui.TextInput(
        label="Message Text",
        style=discord.TextStyle.paragraph,
        placeholder="Enter updated message content...",
        required=False,
        max_length=2000
    )

    def __init__(self, target_message, author_mention):
        super().__init__()
        self.target_message = target_message
        self.author_mention = author_mention

        existing_text = target_message.content.replace(f"{author_mention} ", "").replace(author_mention, "")
        self.new_text.default = existing_text

    async def on_submit(self, interaction: discord.Interaction):
        updated_content = f"{self.author_mention} {self.new_text.value}".strip()
        try:
            await self.target_message.edit(content=updated_content)
            await interaction.response.send_message("✅ Post updated successfully!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to edit post: {e}", ephemeral=True)

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

class AdjustCropView(discord.ui.View):
    def __init__(self, target_message, img_bytes, current_offset, owner_id):
        super().__init__(timeout=120)
        self.target_message = target_message
        self.img_bytes = img_bytes
        self.offset = current_offset
        self.owner_id = owner_id

    def can_manage(self, interaction: discord.Interaction) -> bool:
        is_owner = interaction.user.id == self.owner_id
        is_admin = interaction.user.guild_permissions.administrator if interaction.guild else False
        return is_owner or is_admin

    async def re_crop_and_update(self, interaction: discord.Interaction):
        img = Image.open(io.BytesIO(self.img_bytes))
        width, height = img.size
        
        # Clamp offset so it stays within valid bounds
        max_offset = max(0, height - width)
        self.offset = max(0, min(self.offset, max_offset))

        box = (0, self.offset, width, self.offset + width)
        cropped_img = img.crop(box)

        with io.BytesIO() as image_binary:
            cropped_img.save(image_binary, 'PNG')
            image_binary.seek(0)
            file = discord.File(fp=image_binary, filename="cropped.png")
            
            # Defer the interaction silently to update the image without sending ephemeral spam
            await interaction.response.defer()
            await self.target_message.edit(attachments=[file])

    @discord.ui.button(label="⬆️ Move Up", style=discord.ButtonStyle.secondary)
    async def move_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.can_manage(interaction):
            return await interaction.response.send_message("You cannot adjust this crop.", ephemeral=True)
        self.offset -= 50
        await self.re_crop_and_update(interaction)

    @discord.ui.button(label="⬇️ Move Down", style=discord.ButtonStyle.secondary)
    async def move_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.can_manage(interaction):
            return await interaction.response.send_message("You cannot adjust this crop.", ephemeral=True)
        self.offset += 50
        await self.re_crop_and_update(interaction)

    @discord.ui.button(label="✅ Done", style=discord.ButtonStyle.success)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.can_manage(interaction):
            return await interaction.response.send_message("You cannot adjust this crop.", ephemeral=True)
        await interaction.response.send_message("✅ Crop saved!", ephemeral=True)
        self.stop()

class PostActionView(discord.ui.View):
    def __init__(self, owner_id, img_bytes=None, current_offset=110):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.img_bytes = img_bytes
        self.current_offset = current_offset

    def can_manage(self, interaction: discord.Interaction) -> bool:
        is_owner = interaction.user.id == self.owner_id
        is_admin = interaction.user.guild_permissions.administrator if interaction.guild else False
        return is_owner or is_admin

    @discord.ui.button(label="Adjust Crop", style=discord.ButtonStyle.secondary)
    async def adjust_crop_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.can_manage(interaction):
            if not self.img_bytes:
                return await interaction.response.send_message("Original image data is unavailable for adjustment.", ephemeral=True)
            view = AdjustCropView(interaction.message, self.img_bytes, self.current_offset, self.owner_id)
            await interaction.response.send_message("Use the buttons below to shift the crop position up or down:", view=view, ephemeral=True)
        else:
            await interaction.response.send_message("Only the author or Server Administrators can adjust this crop!", ephemeral=True)

    @discord.ui.button(label="Edit Post", style=discord.ButtonStyle.primary)
    async def edit_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.can_manage(interaction):
            author_mention = f"<@{self.owner_id}>"
            modal = EditPostModal(target_message=interaction.message, author_mention=author_mention)
            await interaction.response.send_modal(modal)
        else:
            await interaction.response.send_message("Only the author or Server Administrators can edit this post!", ephemeral=True)

    @discord.ui.button(label="Delete Post", style=discord.ButtonStyle.danger)
    async def delete_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.can_manage(interaction):
            view = ConfirmDeleteView(original_message=interaction.message)
            await interaction.response.send_message(content="⚠️ **Delete this post?**", view=view, ephemeral=True)
        else:
            await interaction.response.send_message("Only the author or Server Administrators can delete this post!", ephemeral=True)

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
                view = PostActionView(owner_id=message.author.id, img_bytes=img_bytes, current_offset=offset)
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
    # HARD IGNORE: Instantly ignore any message sent by Nymeris
    if message.author.id == 1247291758857949224:
        return

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
            
            async for past_msg in message.channel.history(limit=15, before=message):
                if past_msg.author.id in (1463361569424543898, 1247291758857949224):
                    continue  
                    
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
