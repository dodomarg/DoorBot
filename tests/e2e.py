import json, urllib.request, time, sys

RUN = str(int(time.time()))            # per-run source ids keep the rate limiter clean
SRC, ATTACKER = "test-" + RUN, "attacker-" + RUN
B="http://localhost:8099/api/"
POSTS = ("lock","unlock","stop","verify","dev/jam","keypad/event","keypad/settings",
         "keypad/credentials",
         "calibration","calibration/torque","calibration/goto","calibration/jog",
         "calibration/capture","calibration/reset","codes")
def call(p, body=None, method=None):
    m = method or ("POST" if (body is not None or p in POSTS) else "GET")
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(B+p, data=d, method=m)
    r.add_header("Content-Type","application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as x: return json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e: return {"HTTP":e.code, **json.loads(e.read() or b"{}")}
ok=lambda n,c: print(("PASS " if c else "FAIL ")+n) or (c or sys.exit(1))

# --- clean slate so the suite is re-runnable against a live server ---
for _c in call("codes", method="GET").get("codes", []):
    call("codes/%s" % _c["id"], method="DELETE")
for _c in call("keypad", method="GET").get("credentials", []):
    call("keypad/credentials/%s" % _c["key"], method="DELETE")
call("keypad/settings", {"enabled": True, "action": "unlock",
                         "min_interval_seconds": 0, "known_credentials_only": False})
call("calibration/reset")

# --- calibration wizard ---
call("calibration/torque", {"enabled": False})
call("calibration/goto", {"position": 2400}); time.sleep(0.1)
s=call("calibration/capture", {"which":"locked"})
ok("capture locked", s["calibration"]["locked_position"]>0)
call("calibration/jog", {"delta": -900}); time.sleep(0.1)
s=call("calibration/capture", {"which":"unlocked"})
ok("capture unlocked + auto-calibrated", s["calibrated"] is True)
print("   locked=%s unlocked=%s" % (s["calibration"]["locked_position"], s["calibration"]["unlocked_position"]))

s=call("calibration", {"speed":1500,"auto_lock_seconds":0,"overshoot":40,"hold_ms":100})
ok("save calibration", s["calibration"]["speed"]==1500)

# --- lock / unlock ---
s=call("lock"); ok("lock -> locked", s["state"]=="locked")
s=call("unlock"); ok("unlock -> unlocked", s["state"]=="unlocked")

# --- codes ---
c=call("codes", {"name":"Cleaner","code":"246813","kind":"one_time"})
ok("create one-time code", c.get("id") is not None and c["code_hint"]=="2••••3")
d=call("codes", {"name":"Dup","code":"246813"})
ok("duplicate PIN rejected", d.get("HTTP")==400)
w=call("codes", {"name":"Weak","code":"123"})
ok("short PIN rejected", w.get("HTTP")==400)
r=call("codes", {"name":"Family","code":"778899","kind":"recurring","days_mask":127,"start_minute":0,"end_minute":1439})
ok("create recurring code", r.get("id") is not None)

v=call("verify", {"code":"246813","source":SRC})
ok("one-time code accepted", v.get("allowed") is True and v["status"]["state"]=="unlocked")
v=call("verify", {"code":"246813","source":SRC})
ok("one-time code burns after use", v.get("allowed") is False and v["reason"]=="disabled")
v=call("verify", {"code":"999999","source":SRC})
ok("unknown code rejected", v.get("allowed") is False and v["reason"]=="unknown_code")
v=call("verify", {"code":"778899","source":SRC})
ok("recurring code accepted", v.get("allowed") is True)

# --- rate limiting (5 fails then lockout) ---
for i in range(5): call("verify", {"code":"000111","source":ATTACKER})
v=call("verify", {"code":"778899","source":ATTACKER})
ok("rate limit kicks in", v.get("HTTP")==429)
v=call("verify", {"code":"778899","source":SRC})
ok("other sources unaffected", v.get("allowed") is True)

# --- keypad (encrypted bridge: method + credential slot) ---
call("keypad/settings", {"enabled":True,"action":"unlock","min_interval_seconds":0,
                         "known_credentials_only":False})
call("lock")

# An unnamed slot is allowed while known_credentials_only is off.
e=call("keypad/event", {"method":"pin","slot":7,"keypad":"Front door","battery":88})
ok("unknown slot allowed by default", e["result"]=="accepted" and e["known"] is False)
ok("keypad unlocked the door", call("status")["state"]=="unlocked")

# Name a slot and it becomes identifiable.
c=call("keypad/credentials", {"name":"Maya","method":"fingerprint","slot":0,
                              "days_mask":127,"start_minute":0,"end_minute":1439})
ok("create credential", c["credential"]["key"]=="fingerprint:0")
call("lock")
e=call("keypad/event", {"method":"fingerprint","slot":0})
ok("named credential identified", e["name"]=="Maya" and e["result"]=="accepted")
ok("method label surfaced", e["method_label"]=="Fingerprint")
ok("credential unlocked the door", call("status")["state"]=="unlocked")

# Methods are distinct namespaces: fingerprint slot 0 != pin slot 0.
call("lock")
e=call("keypad/event", {"method":"pin","slot":0})
ok("method namespaces are separate", e["known"] is False)

# The raw method byte from a decrypted frame is accepted too (0x0C = fingerprint).
call("lock")
e=call("keypad/event", {"method":12,"slot":0})
ok("raw method byte decoded", e["method"]=="fingerprint" and e["name"]=="Maya")

# Locking down to known credentials only.
call("keypad/settings", {"enabled":True,"action":"unlock","min_interval_seconds":0,
                         "known_credentials_only":True})
call("lock")
e=call("keypad/event", {"method":"nfc","slot":42})
ok("unknown slot refused when locked down", e["result"]=="rejected" and e["acted"] is False)
ok("door stayed locked", call("status")["state"]=="locked")

# A disabled credential is refused even though the keypad accepted it.
call("keypad/credentials", {"name":"Maya","method":"fingerprint","slot":0,
                            "enabled":False,"days_mask":127,
                            "start_minute":0,"end_minute":1439})
e=call("keypad/event", {"method":"fingerprint","slot":0})
ok("disabled credential refused", e["result"]=="rejected")

# Out-of-window credential is refused. Pick a window on the far side of the
# clock from "now" so this never straddles the current minute.
_lt = time.localtime()
_now_min = _lt.tm_hour * 60 + _lt.tm_min
_start, _end = (780, 840) if _now_min < 720 else (60, 120)
call("keypad/credentials", {"name":"Cleaner","method":"pin","slot":3,"enabled":True,
                            "days_mask":127,"start_minute":_start,"end_minute":_end})
e=call("keypad/event", {"method":"pin","slot":3})
ok("out-of-hours credential refused",
   e["result"]=="rejected" and "hours" in e["reason"])

# Wrong-day credential is refused (mask with today's bit cleared).
_today_bit = 1 << _lt.tm_wday
call("keypad/credentials", {"name":"Weekday only","method":"nfc","slot":5,"enabled":True,
                            "days_mask":127 & ~_today_bit,
                            "start_minute":0,"end_minute":1439})
e=call("keypad/event", {"method":"nfc","slot":5})
ok("wrong-day credential refused",
   e["result"]=="rejected" and "today" in e["reason"])

# Duress flag rides along.
call("keypad/credentials", {"name":"Panic","method":"pin","slot":9,"enabled":True,
                            "duress":True,"days_mask":127,
                            "start_minute":0,"end_minute":1439})
e=call("keypad/event", {"method":"pin","slot":9})
ok("duress credential flagged", e["duress"] is True and e["result"]=="accepted")

# Debounce a repeated frame.
call("keypad/settings", {"enabled":True,"action":"unlock","min_interval_seconds":30,
                         "known_credentials_only":False})
call("keypad/event", {"method":"pin","slot":9})
e=call("keypad/event", {"method":"pin","slot":9})
ok("repeat frame debounced", e["throttled"] is True and e["acted"] is False)
call("keypad/settings", {"enabled":True,"action":"unlock","min_interval_seconds":0,
                         "known_credentials_only":False})

# Credential listing + delete.
kp=call("keypad", method="GET")
# fingerprint:0 was saved twice, so it updates in place rather than duplicating.
ok("credentials listed", len(kp["credentials"])==4)
call("keypad/credentials/pin:9", method="DELETE")
ok("credential deleted", len(call("keypad", method="GET")["credentials"])==3)
ok("deleting an unknown credential 404s",
   call("keypad/credentials/pin:9", method="DELETE").get("HTTP")==404)

# Disabling the keypad refuses everything.
call("keypad/settings", {"enabled":False})
e=call("keypad/event", {"method":"pin","slot":3})
ok("disabled keypad refuses", e["result"]=="rejected")
call("keypad/settings", {"enabled":True,"action":"unlock","min_interval_seconds":0})

# --- jam handling ---
call("dev/jam", {"enabled":True})
j=call("unlock")
ok("jam detected", j.get("HTTP")==409)
call("dev/jam", {"enabled":False})
ok("state sticky jammed", call("status")["state"]=="jammed")
ok("recovers after clearing jam", call("unlock")["state"]=="unlocked")

ok("events logged", len(call("events?limit=100")["events"])>10)
print("\nALL TESTS PASSED")
