import os
import platform
import subprocess
import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# --- CONFIGURATION ---
EXCEL_FILE = "cameras.xlsx"
CHECK_INTERVAL_SECONDS = 60  # Check every 60 seconds

print("Setting up an isolated Chrome instance for WhatsApp...")

# Set up clean Chrome options to prevent profile lock conflicts
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument("--start-maximized")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")

# Launch the driver
driver = webdriver.Chrome(options=chrome_options)
driver.get("https://web.whatsapp.com")

print("\n" + "=" * 60)
print("👉 ACTION REQUIRED:")
print("1. Scan the QR code on the screen to log into WhatsApp Web.")
print("2. Wait until your chats completely load.")
print("3. Come back to this terminal and press ENTER to start the bot.")
print("=" * 60 + "\n")

input("Press ENTER here only after WhatsApp Web is fully loaded...")

def send_whatsapp_single_tab(device_name, ip_address, status, target_phone):
    """Reuses the single open Chrome tab to route the message without crashing on emojis."""
    # Replaced emojis with text markers to prevent the BMP ChromeDriver error
    status_marker = "!!! ALERT !!!" if status == "OFFLINE" else "=== RECOVERED ==="
    
    message_body = (
        f"*{status_marker}*\n\n"
        f"*Device:* {device_name}\n"
        f"*IP Address:* {ip_address}\n"
        f"*Status:* {status}\n"
        f"*Time:* {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    try:
        clean_phone = (
            str(target_phone).replace("+", "").replace(" ", "").strip()
        )
        chat_url = f"https://web.whatsapp.com/send?phone={clean_phone}"
        print(f"Navigating to chat for {device_name} ({clean_phone})...")
        driver.get(chat_url)

        # Wait up to 30 seconds for WhatsApp's text field to load
        chat_box = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[@contenteditable='true']")
            )
        )

        chat_box.click()
        time.sleep(1)

        # Send lines safely handling text blocks
        for line in message_body.split("\n"):
            chat_box.send_keys(line)
            chat_box.send_keys(Keys.SHIFT + Keys.ENTER)

        # Press Enter to transmit message
        chat_box.send_keys(Keys.ENTER)
        print(f"✅ Alert successfully pushed to {target_phone}")
        time.sleep(3)  # Give it a moment to dispatch over the network

    except Exception as e:
        print(f"❌ Failed to deliver message to {target_phone}: {e}")

def ping_ip(ip):
    """Directly pings camera across switches/NanoStations."""
    param = "-n" if platform.system().lower() == "windows" else "-c"
    # Timeout set to 1000ms (1 second) to detect link drops quickly
    command = ["ping", param, "1", "-w", "1000", str(ip).strip()]
    return subprocess.run(command, stdout=subprocess.DEVNULL).returncode == 0

def load_cameras_from_excel(file_path):
    """Reads camera layout from local Excel file."""
    try:
        df = pd.read_excel(file_path)
        return df[
            ["Device Name", "IP Address", "Contact Number"]
        ].to_dict(orient="records")
    except Exception as e:
        print(f"⚠️ Error reading '{file_path}': {e}")
        return []

def main():
    print("\nBot running. Press Ctrl+C in this terminal window to stop.")
    camera_states = {}

    try:
        while True:
            cameras = load_cameras_from_excel(EXCEL_FILE)

            if not cameras:
                print("Excel data empty or unreadable. Retrying in 10s...")
                time.sleep(10)
                continue

            for cam in cameras:
                name = cam["Device Name"]
                ip = cam["IP Address"]
                phone = cam["Contact Number"]

                if pd.isna(phone) or pd.isna(ip):
                    continue

                # Run direct network ping
                is_online = ping_ip(ip)

                # Initialize camera state on script bootup
                if ip not in camera_states:
                    camera_states[ip] = is_online
                    print(
                        f"Monitoring: {name} [{ip}] -> Contact: {phone} "
                        f"({'ONLINE' if is_online else 'OFFLINE'})"
                    )
                    continue

                # Event: Camera drops offline
                if camera_states[ip] and not is_online:
                    print(f"🚨 ALERT: {name} ({ip}) went OFFLINE!")
                    send_whatsapp_single_tab(name, ip, "OFFLINE", phone)
                    camera_states[ip] = False

                # Event: Camera recovers back online
                elif not camera_states[ip] and is_online:
                    print(f"ℹ️ RECOVERY: {name} ({ip}) is back ONLINE.")
                    send_whatsapp_single_tab(name, ip, "RECOVERED", phone)
                    camera_states[ip] = True

            time.sleep(CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\nStopping monitor service cleanly...")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()