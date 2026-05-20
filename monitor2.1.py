import os
import platform
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ================= CONFIG =================
EXCEL_FILE = "cameras.xlsx"
CHECK_INTERVAL_SECONDS = 30
MAX_THREADS = 25
FAILURE_THRESHOLD = 2  # 2 failed scans = OFFLINE

print("Starting WhatsApp Web...")
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument("--start-maximized")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(options=chrome_options)
driver.get("https://web.whatsapp.com")
input("Scan QR and press ENTER to start monitoring...")


# ================= WHATSAPP OPTIMIZATION =================
def send_whatsapp(name, ip, status, phone):
    try:
        phone = str(phone).replace("+", "").replace(" ", "").split(".")[0]  # Clean float anomalies from excel
        msg = f"*{status} ALERT*\n\nDevice: {name}\nIP: {ip}\nStatus: {status}\nTime: {time.strftime('%Y-%m-%d %H:%M:%S')}"

        # Note: This still triggers a reload but includes a try/catch safety net
        driver.get(f"https://web.whatsapp.com/send?phone={phone}")

        box = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true']"))
        )
        box.click()

        # Efficient multi-line clipboard-style simulation via structural split
        for line in msg.split("\n"):
            if line.strip():
                box.send_keys(line)
            box.send_keys(Keys.SHIFT + Keys.ENTER)

        box.send_keys(Keys.ENTER)
        time.sleep(3)  # Give WhatsApp a moment to actually broadcast the packet
        print(f"Sent alert to {phone}")
    except Exception as e:
        print(f"WhatsApp Alert Delivery Failed for {name}: {e}")


# ================= FAST PING =================
def ping(ip):
    is_windows = platform.system().lower() == "windows"
    cmd = ["ping", "-n", "1", "-w", "1000", str(ip)] if is_windows else ["ping", "-c", "1", "-W", "1", str(ip)]
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


# ================= SMART LOAD EXCEL =================
_last_mtime = 0
_cached_devices = []


def load_devices_if_changed():
    """Only reads from storage disk if the file has been altered."""
    global _last_mtime, _cached_devices
    try:
        if os.path.exists(EXCEL_FILE):
            current_mtime = os.path.getmtime(EXCEL_FILE)
            if current_mtime != _last_mtime:
                df = pd.read_excel(EXCEL_FILE)
                _cached_devices = df.to_dict("records")
                _last_mtime = current_mtime
                print("🔄 Excel configurations reloaded.")
    except Exception as e:
        print(f"⚠️ Error reading excel file (might be locked/open): {e}")
    return _cached_devices


# ================= WORKER =================
def check_device(device):
    try:
        ip = str(device.get("IP Address", "")).strip()
        name = device.get("Device Name", "Unknown Device")
        phone = device.get("Contact Number", "")
        if not ip or pd.isna(device.get("IP Address")):
            return None
        return {"name": name, "ip": ip, "phone": phone, "online": ping(ip)}
    except Exception as e:
        print(f"Worker processing error: {e}")
        return None


# ================= MAIN RUNNER =================
def main():
    device_state = {}
    failure_count = {}
    print("🚀 Monitoring loop fully engaged...")

    while True:
        try:
            devices = load_devices_if_changed()
            if not devices:
                time.sleep(5)
                continue

            results = []
            with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
                futures = [executor.submit(check_device, d) for d in devices]
                for f in as_completed(futures):
                    res = f.result()
                    if res:
                        results.append(res)

            # ===== PROCESS RESULTS =====
            for r in results:
                ip, name, phone, online = r["ip"], r["name"], r["phone"], r["online"]

                if ip not in device_state:
                    device_state[ip] = online
                    failure_count[ip] = 0
                    print(f"INIT {name} ({ip}) → {'ONLINE' if online else 'OFFLINE'}")
                    continue

                if online:
                    if not device_state[ip]:
                        print(f"💚 RECOVERED: {name}")
                        send_whatsapp(name, ip, "RECOVERED", phone)
                    device_state[ip] = True
                    failure_count[ip] = 0
                else:
                    failure_count[ip] += 1
                    print(f"⚠️ FAIL {name} ({failure_count[ip]}/{FAILURE_THRESHOLD})")

                    if failure_count[ip] == FAILURE_THRESHOLD and device_state[ip]:
                        print(f"🚨 ALERT OFFLINE: {name}")
                        send_whatsapp(name, ip, "OFFLINE", phone)
                        device_state[ip] = False

        except Exception as e:
            print(f"CRITICAL failure inside loop execution context: {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()