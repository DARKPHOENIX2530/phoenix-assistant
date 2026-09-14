import time
import requests
import config
import assistant

def main():
    cfg = config.load()
    print("=" * 60)
    print("  PHOENIX TELEGRAM BRIDGE")
    print("=" * 60)
    
    token = input("Enter your Telegram Bot Token: ").strip()
    if not token:
        print("No token provided. Exiting.")
        return
        
    bot = assistant.Phoenix(cfg)
    print("\n[telegram] Connecting to Telegram API...")
    
    offset = 0
    url = f"https://api.telegram.org/bot{token}"
    
    try:
        # Check token validity
        me = requests.get(f"{url}/getMe").json()
        if not me.get("ok"):
            print("[telegram] Invalid token!")
            return
        print(f"[telegram] Successfully logged in as @{me['result']['username']}")
        print("[telegram] Listening for messages. Press Ctrl+C to stop.")
        
        while True:
            try:
                resp = requests.get(f"{url}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=40).json()
                if not resp.get("ok"):
                    time.sleep(2)
                    continue
                    
                for update in resp.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = msg.get("chat", {}).get("id")
                    
                    if text and chat_id:
                        print(f"\n[user] {text}")
                        reply = bot.handle(text)
                        if reply:
                            print(f"[phoenix] {reply}")
                            requests.post(f"{url}/sendMessage", json={"chat_id": chat_id, "text": reply})
                            
            except requests.Timeout:
                continue
            except Exception as e:
                print(f"[telegram] Error: {e}")
                time.sleep(5)
                
    except KeyboardInterrupt:
        print("\n[telegram] Shutting down bridge.")

if __name__ == "__main__":
    main()
