from groq import Groq
from pydantic import BaseModel
from discord.ext import commands
import discord


class Settings(BaseModel):
    token: str
    prefix: str
    apikey: str


with open("config.json", "r") as f:
    data = Settings.model_validate_json(f.read())

intents = discord.Intents.default()
intents.message_content = True

client = Groq(api_key=data.apikey)
bot = commands.Bot(command_prefix=data.prefix, intents=intents)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")


# would be nice to have commands for daily bot use


# will try to act like a really person but should be smart to not respond to all messages sent but to the ones it wants to and have the ability to do replies to a message not just type
@bot.on_message
async def msg() -> None: ...


bot.run(token=data.token)
