import platform
import subprocess
import time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# ================= CONFIG =================

EXCEL_FILE = "cameras.xlsx"
CHECK_INTERVAL_SECONDS = 30
MAX_THREADS = 25
FAILURE_THRESHOLD = 2   # 2 failed scans = OFFLINE


# ================= SELENIUM =================

print("Starting WhatsApp Web...")

chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument("--start-maximized")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(options=chrome_options)
driver.get("https://web.whatsapp.com")

input("Scan QR and press ENTER...")


# ================= WHATSAPP =================

def send_whatsapp(name, ip, status, phone):

    try:
        phone = str(phone).replace("+", "").replace(" ", "")

        msg = f"""
*{status} ALERT*

Device: {name}
IP: {ip}
Status: {status}
Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
"""

        driver.get(f"https://web.whatsapp.com/send?phone={phone}")

        box = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true']"))
        )

        box.click()

        for line in msg.split("\n"):
            box.send_keys(line)
            box.send_keys(Keys.SHIFT + Keys.ENTER)

        box.send_keys(Keys.ENTER)

        time.sleep(2)

        print(f"Sent to {phone}")

    except Exception as e:
        print("WhatsApp error:", e)


# ================= FAST PING =================

def ping(ip):
    is_windows = platform.system().lower() == "windows"

    if is_windows:
        cmd = ["ping", "-n", "1", "-w", "1000", str(ip)]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", str(ip)]

    return subprocess.run(cmd, stdout=subprocess.DEVNULL).returncode == 0


# ================= LOAD EXCEL =================

def load_devices():
    df = pd.read_excel(EXCEL_FILE)
    return df.to_dict("records")


# ================= WORKER =================

def check_device(device):
    ip = str(device["IP Address"]).strip()
    return {
        "name": device["Device Name"],
        "ip": ip,
        "phone": device["Contact Number"],
        "online": ping(ip)
    }


# ================= MAIN =================

def main():

    device_state = {}
    failure_count = {}

    print("Monitoring started...")

    while True:

        devices = load_devices()
        results = []

        # ===== PARALLEL PING =====
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures = [executor.submit(check_device, d) for d in devices]

            for f in as_completed(futures):
                results.append(f.result())

        # ===== PROCESS RESULTS =====
        for r in results:

            ip = r["ip"]
            name = r["name"]
            phone = r["phone"]
            online = r["online"]

            # INIT
            if ip not in device_state:
                device_state[ip] = online
                failure_count[ip] = 0
                print(f"INIT {name} → {'ONLINE' if online else 'OFFLINE'}")
                continue

            # ONLINE
            if online:
                failure_count[ip] = 0

                if not device_state[ip]:
                    print(f"RECOVERED {name}")
                    send_whatsapp(name, ip, "RECOVERED", phone)
                    device_state[ip] = True

            # OFFLINE
            else:
                failure_count[ip] += 1

                print(f"FAIL {name} ({failure_count[ip]})")

                if failure_count[ip] >= FAILURE_THRESHOLD and device_state[ip]:
                    print(f"ALERT OFFLINE {name}")
                    send_whatsapp(name, ip, "OFFLINE", phone)
                    device_state[ip] = False

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()