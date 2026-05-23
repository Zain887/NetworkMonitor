import os
import platform
import queue
import subprocess
import threading
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

# Thread-safe queue to pass alerts from ping threads to the Selenium main thread
alert_queue = queue.Queue()

# ================= PERSISTENT WHATSAPP SESSION =================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(SCRIPT_DIR, "whatsapp_selenium_profile")

print("Starting WhatsApp Web with Persistent Profile...")
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument("--start-maximized")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument(f"--user-data-dir={PROFILE_DIR}")

driver = webdriver.Chrome(options=chrome_options)
driver.get("https://web.whatsapp.com")

try:
    print("Checking if session is already authenticated...")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, "//div[@id='pane-side']"))
    )
    print("✅ Authenticated automatically via cached user session data!")
except Exception:
    print("\n⚠️ Authentication token missing or expired.")
    input("👉 Please scan the QR code displayed on screen, wait for chats to load, then press ENTER here...")


# ================= UTILITIES =================
def clean_phone_number(phone_raw):
    """Safely normalizes numbers from Excel, cleaning floats or symbols."""
    if pd.isna(phone_raw):
        return None
    if isinstance(phone_raw, float):
        phone_raw = int(phone_raw)
    phone_str = str(phone_raw).strip()
    return "".join(c for c in phone_str if c.isdigit())


# ================= WHATSAPP DISPATCHER (SINGLE-THREADED) =================
def process_alert_queue():
    """Consumes items from the queue sequentially on a single thread."""
    while not alert_queue.empty():
        alert = alert_queue.get()
        name = alert["name"]
        ip = alert["ip"]
        status = alert["status"]
        phone = clean_phone_number(alert["phone"])

        if not phone:
            print(f"❌ Missing or corrupt phone data for {name}. Alert aborted.")
            alert_queue.task_done()
            continue

        try:
            msg = f"*{status} ALERT*\n\nDevice: {name}\nIP: {ip}\nStatus: {status}\nTime: {time.strftime('%Y-%m-%d %H:%M:%S')}"
            chat_url = f"https://web.whatsapp.com/send?phone={phone}"
            print(f"Opening chat payload viewport for {name} ({phone})...")
            driver.get(chat_url)

            box = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@contenteditable='true'][@aria-label='Type a message' or @data-tab='10']"))
            )
            box.click()
            time.sleep(0.5)

            script = """
            const dataTransfer = new DataTransfer();
            dataTransfer.setData('text/plain', arguments[0]);
            const event = new ClipboardEvent('paste', { clipboardData: dataTransfer, bubbles: true });
            arguments[1].dispatchEvent(event);
            """
            driver.execute_script(script, msg, box)
            
            time.sleep(1)
            box.send_keys(Keys.ENTER)
            time.sleep(3.0)  # Wait for packet stream delivery
            print(f"✅ Alert successfully pushed to {phone}")
            
        except Exception as e:
            print(f"❌ WhatsApp Delivery Failed for {name}: {e}")
        
        alert_queue.task_done()


# ================= FAST PING =================
def ping(ip):
    is_windows = platform.system().lower() == "windows"
    cmd = ["ping", "-n", "1", "-w", "1000", str(ip)] if is_windows else ["ping", "-c", "1", "-W", "1", str(ip)]
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


# ================= SMART LOAD EXCEL =================
_last_mtime = 0
_cached_devices = []
config_changed = False

def load_devices_if_changed():
    global _last_mtime, _cached_devices, config_changed
    config_changed = False
    try:
        if os.path.exists(EXCEL_FILE):
            current_mtime = os.path.getmtime(EXCEL_FILE)
            if current_mtime != _last_mtime:
                df = pd.read_excel(EXCEL_FILE, dtype={"Contact Number": str, "IP Address": str, "Device Type": str})
                _cached_devices = df.to_dict("records")
                _last_mtime = current_mtime
                config_changed = True
                print("🔄 Excel configurations reloaded.")
    except Exception as e:
        print(f"⚠️ Error reading excel file (might be locked/open): {e}")
    return _cached_devices, config_changed


# ================= WORKER =================
def check_device(device):
    try:
        ip = str(device.get("IP Address", "")).strip()
        name = device.get("Device Name", "Unknown Device")
        phone = device.get("Contact Number", "")
        dtype = str(device.get("Device Type", "Device")).strip()
        if not ip or pd.isna(device.get("IP Address")) or ip == "nan":
            return None
        return {"name": name, "ip": ip, "phone": phone, "type": dtype, "online": ping(ip)}
    except Exception as e:
        print(f"Worker processing error: {e}")
        return None


# ================= MAIN RUNNER =================
def main():
    device_state = {}
    failure_count = {}
    print("🚀 Monitoring loop fully engaged...")

    try:
        while True:
            try:
                devices, updated = load_devices_if_changed()
                if not devices:
                    time.sleep(5)
                    continue

                if updated:
                    active_ips = {str(d.get("IP Address", "")).strip() for d in devices if pd.notna(d.get("IP Address"))}
                    orphaned_ips = set(device_state.keys()) - active_ips
                    for old_ip in orphaned_ips:
                        print(f"🧹 Purging dropped device {old_ip} from memory.")
                        device_state.pop(old_ip, None)
                        failure_count.pop(old_ip, None)

                results = []
                with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
                    futures = [executor.submit(check_device, d) for d in devices]
                    for f in as_completed(futures):
                        res = f.result()
                        if res:
                            results.append(res)

                # ===== PROCESS RESULTS AND ENQUEUE ALERTS =====
                for r in results:
                    ip, name, phone, dtype, online = r["ip"], r["name"], r["phone"], r["type"], r["online"]

                    if ip not in device_state:
                        device_state[ip] = online
                        failure_count[ip] = 0
                        print(f"INIT {name} ({ip}) → {'ONLINE' if online else 'OFFLINE'}")
                        continue

                    if online:
                        if not device_state[ip]:
                            print(f"💚 RECOVERED: {name} (Queueing Alert)")
                            alert_queue.put({"name": name, "ip": ip, "status": "RECOVERED", "phone": phone})
                        device_state[ip] = True
                        failure_count[ip] = 0
                    else:
                        failure_count[ip] += 1
                        print(f"⚠️ FAIL {name} ({failure_count[ip]}/{FAILURE_THRESHOLD})")

                        if failure_count[ip] == FAILURE_THRESHOLD and device_state[ip]:
                            device_state[ip] = False
                            
                            # SMART INTERCEPTION LOGIC FOR VOIP NETWORK ONLY
                            if ip.startswith("192.168.3.") and ip != "192.168.3.2":
                                print(f"🔍 VoIP extension failure detected ({name}). Checking GrandStream Main Server status...")
                                
                                # Double check the main GrandStream controller status
                                main_server_online = ping("192.168.3.2")
                                if not main_server_online:
                                    print("🚨 CRITICAL: GrandStream Main Server is unresponsive! Injecting Main server alert instead.")
                                    alert_queue.put({
                                        "name": "GrandStream UCM6108",
                                        "ip": "192.168.3.2",
                                        "status": "GrandStram UCM6108 is Hang Need to be Restart Device",
                                        "phone": phone
                                    })
                                    continue # Stops the individual phone alert from sending
                            
                            # DEFAULT ALERTS FOR CAMERAS OR INDIVIDUAL VOIP DROPS
                            print(f"🚨 OFFLINE: {name} (Queueing Alert)")
                            status_msg = "Hang Need to be Restart Device" if ip == "192.168.3.2" else "OFFLINE"
                            alert_queue.put({"name": name, "ip": ip, "status": status_msg, "phone": phone})

                if not alert_queue.empty():
                    print(f"📦 Processing {alert_queue.qsize()} pending notification(s)...")
                    process_alert_queue()

            except Exception as e:
                print(f"CRITICAL failure inside loop execution context: {e}")

            time.sleep(CHECK_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        print("\n🛑 Manual shutdown signal caught. Cleaning resources...")
    finally:
        try:
            driver.quit()
            print("🔒 Chrome session killed safely.")
        except NameError:
            pass


if __name__ == "__main__":
    main()