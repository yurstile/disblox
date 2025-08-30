from bot_manager import bot_manager

def run_bot():
    bot = bot_manager.create_bot()
    bot_manager.start_bot()

if __name__ == "__main__":
    run_bot()