#!/usr/bin/python
# -*- coding: utf-8 -*-

import time
import network
import urequests
import uasyncio as asyncio
import os
import secrets              # ← Ensure this is imported

from machine import Pin, RTC, reset
from microdot import Microdot, Response

# ————— Configuration —————
worldtimeurl        = "https://timeapi.io/api/TimeZone/zone?timeZone=Australia/Canberra"
pulsefrequency      = 60        # seconds per pulse = 1 minute
wifi_retry_interval = 600       # retry Wi-Fi every 10 minutes
PICO_IP_SUBNET      = "192.168.50."

# ————— Startup Delay —————
time.sleep(5)
print("=== main.py loaded ===")

# ————— Helper Functions —————

def format_time(t):
    year, month, day, hour, minute, second, *_ = t
    return f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

def print_gmt_and_local_time(url):
    print("[DEBUG] print_gmt_and_local_time()")
    print("  GMT time:", format_time(time.gmtime()), "(UTC)")
    try:
        resp = urequests.get(url)
        if resp.status_code != 200:
            print("  Failed to fetch local time (status", resp.status_code, ")")
            return
        data = resp.json()
        dt = data["currentLocalTime"]
        tz = data["timeZone"]
        y = int(dt[0:4]); mo = int(dt[5:7]); d = int(dt[8:10])
        h = int(dt[11:13]); mi = int(dt[14:16]); s = int(dt[17:19])
        print("  Local time:", format_time((y,mo,d,h,mi,s,0,0)), f"({tz})")
    except Exception as e:
        print("  Error fetching local time:", e)

def set_time(url, wlan):
    print("[DEBUG] set_time() start")
    wlan.connect(secrets.SSID, secrets.PASSWORD)
    retries = 0
    while not wlan.isconnected() and retries < 10:
        time.sleep(2)
        retries += 1
        print(f"[DEBUG] Wi-Fi retry {retries}/10")
    if not wlan.isconnected():
        print("[ERROR] Could not connect to Wi-Fi")
        return False
    ip = wlan.ifconfig()[0]
    print(f"[DEBUG] Connected to Wi-Fi, IP = {ip}")
    try:
        print_gmt_and_local_time(url)
        resp = urequests.get(url)
        data = resp.json()
        dt = data["currentLocalTime"]
        y = int(dt[0:4]); mo = int(dt[5:7]); d = int(dt[8:10])
        h = int(dt[11:13]); mi = int(dt[14:16]); s = int(dt[17:19])
        RTC().datetime((y, mo, d, 0, h, mi, s, 0))
        print("[DEBUG] RTC updated")
    except Exception as e:
        print("[ERROR] Error setting RTC:", e)
        return False
    return True  # keep Wi-Fi up for web server

def twodigits(n):
    s = str(n)
    return s if len(s) == 2 else "0" + s

def pulsessince12(timestr):
    parts = timestr.split(":")
    if len(parts) < 2:
        raise ValueError("Invalid time string")
    h = int(parts[0]); m = int(parts[1])
    seconds = (h % 12) * 3600 + m * 60
    return seconds // pulsefrequency

def pulsetoclock(last_hm, a, b):
    print(f"[DEBUG] pulsetoclock() last='{last_hm}' a={a} b={b}")
    a = not a; b = not b
    clock1(int(a)); clock2(int(b))
    led = Pin("LED", Pin.OUT); led.on()
    time.sleep(1)
    clock1(0); clock2(0); led.off()
    h, m = map(int, last_hm.split(":"))
    m += 1
    h = (h + m // 60) % 12
    m %= 60
    new_hm = f"{twodigits(h)}:{twodigits(m)}"
    try:
        with open("lastpulseat.txt", "w") as f:
            f.write(f"{new_hm}\t{a}\t{b}")
    except Exception as e:
        print("[ERROR] writing lastpulseat.txt:", e)
    time.sleep(0.5)
    print(f"[DEBUG] pulsetoclock() new='{new_hm}' a={a} b={b}")
    return new_hm, a, b

def calcoffset(current_hm):
    # Only log errors or bootstrap; normal zero-offsets are silent
    try:
        data = open("lastpulseat.txt").read().strip().split("\t")
        last_hm, a_str, b_str = data
        a = a_str.lower() == "true"
        b = b_str.lower() == "true"
        last_p = pulsessince12(last_hm)
    except OSError:
        print("[DEBUG] Bootstrapping lastpulseat.txt from firstruntime.txt")
        try:
            last_hm = open("firstruntime.txt").read().strip()
        except OSError:
            print("[ERROR] firstruntime.txt missing")
            return None, None, None, None
        last_p = pulsessince12(last_hm)
        a, b = False, True
        try:
            with open("lastpulseat.txt", "w") as f:
                f.write(f"{last_hm}\t{a}\t{b}")
        except Exception as e:
            print("[ERROR] bootstrapping lastpulseat.txt:", e)
    current_p = pulsessince12(current_hm)
    offset = current_p - last_p
    return offset, last_hm, a, b

# ————— GPIO Setup —————
clock2 = Pin(14, Pin.OUT, value=0)
clock1 = Pin(13, Pin.OUT, value=0)

# ————— Web Server (Microdot) —————
Response.default_content_type = "text/html"
app = Microdot()

@app.before_request
def restrict(request):
    client_ip = request.client_addr[0]
    allowed = client_ip.startswith(PICO_IP_SUBNET)
    print(f"[DEBUG] Request from {client_ip}, allowed={allowed}")
    if not allowed:
        return Response("Forbidden", status_code=403)

@app.route('/')
def index(request):
    print("[DEBUG] GET /")
    now = time.localtime()
    current_hm = f"{twodigits(now[3])}:{twodigits(now[4])}"
    try:
        base = open("firstruntime.txt").read().strip()
    except OSError:
        base = "(not set)"
    return f"""
<html>
  <head><title>Clock Control</title></head>
  <body>
    <h1>Clock Control Panel</h1>
    <p><strong>Current Time:</strong> {current_hm}</p>
    <p><strong>Baseline:</strong> {base}</p>
    <form action="/sync" method="post">
      <input type="time" name="initial_time" step="60" required>
      <button>Synchronise Clock</button>
    </form>
    <form action="/advance1" method="post">
      <button>+1 minute</button>
    </form>
    <form action="/advance5" method="post">
      <button>+5 minutes</button>
    </form>
  </body>
</html>
"""

@app.post('/sync')
def sync_clock(request):
    new = request.form.get('initial_time')
    print(f"[DEBUG] POST /sync initial_time={new}")
    if not new:
        return Response("No time provided.", 400)
    parts = new.split(":")
    try:
        h, m = map(int, parts)
        assert 0 <= h < 24 and 0 <= m < 60
    except:
        return Response("Invalid time. Use HH:MM.", 400)
    try:
        with open("firstruntime.txt", "w") as f:
            f.write(new)
    except Exception as e:
        return Response(f"Error writing baseline: {e}", 500)
    try:
        os.remove("lastpulseat.txt")
    except OSError:
        pass
    now = time.localtime()
    now_hm = f"{twodigits(now[3])}:{twodigits(now[4])}"
    offset, last_hm, a, b = calcoffset(now_hm)
    if offset is None:
        return Response("Synchronization failed.", 500)
    if offset != 0:
        print(f"[DEBUG] sync_clock: offset={offset}, pulsing...")
        for _ in range(max(0, offset)):
            last_hm, a, b = pulsetoclock(last_hm, a, b)
    return index(request)

@app.post('/advance1')
def advance_one(request):
    print("[DEBUG] POST /advance1")
    try:
        last_hm, a_str, b_str = open("lastpulseat.txt").read().strip().split("\t")
        a = a_str.lower() == "true"
        b = b_str.lower() == "true"
    except:
        try:
            last_hm = open("firstruntime.txt").read().strip()
        except:
            return Response("No baseline to advance.", 500)
        a, b = False, True
    pulsetoclock(last_hm, a, b)
    return index(request)

@app.post('/advance5')
def advance_five(request):
    print("[DEBUG] POST /advance5")
    for _ in range(5):
        advance_one(request)
    return index(request)

async def clock_loop():
    print("[DEBUG] clock_loop starting")
    global wlan, wifi_failed, last_wifi_attempt
    while True:
        now = time.localtime()
        rtc_hm = f"{twodigits(now[3])}:{twodigits(now[4])}"
        offset, last_hm, a, b = calcoffset(rtc_hm)
        if offset is not None and offset != 0:
            print(f"[DEBUG] clock_loop: offset={offset}, pulsing...")
            pulsetoclock(last_hm, a, b)
        if wifi_failed and (time.time() - last_wifi_attempt) > wifi_retry_interval:
            print("[DEBUG] retrying Wi-Fi sync")
            if set_time(worldtimeurl, wlan):
                wifi_failed = False
        await asyncio.sleep(0.1)

def main():
    print("[DEBUG] Entering main()")
    led = Pin("LED", Pin.OUT)
    led.on(); time.sleep(1); led.off()
    print("[DEBUG] Startup RTC:", time.gmtime())
    print("[DEBUG] Connecting to Wi-Fi…")
    global wlan, wifi_failed, last_wifi_attempt
    wlan = network.WLAN(network.STA_IF); wlan.active(True)
    wifi_failed = not set_time(worldtimeurl, wlan)
    last_wifi_attempt = time.time()
    print("[DEBUG] Launching asyncio runner")
    async def runner():
        print("[DEBUG] Creating server & clock tasks")
        server = asyncio.create_task(app.start_server(host="0.0.0.0", port=80))
        clock  = asyncio.create_task(clock_loop())
        print("[DEBUG] Tasks created")
        await asyncio.gather(server, clock)
    asyncio.run(runner())

if __name__ == "__main__":
    main()

