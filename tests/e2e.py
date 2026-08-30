import json, urllib.request, time, sys
B="http://localhost:8099/api/"
POSTS = ("lock","unlock","stop","verify","dev/jam","keypad/event","keypad/settings",
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

v=call("verify", {"code":"246813","source":"test"})
ok("one-time code accepted", v.get("allowed") is True and v["status"]["state"]=="unlocked")
v=call("verify", {"code":"246813","source":"test"})
ok("one-time code burns after use", v.get("allowed") is False and v["reason"]=="disabled")
v=call("verify", {"code":"999999","source":"test"})
ok("unknown code rejected", v.get("allowed") is False and v["reason"]=="unknown_code")
v=call("verify", {"code":"778899","source":"test"})
ok("recurring code accepted", v.get("allowed") is True)

# --- rate limiting (5 fails then lockout) ---
for i in range(5): call("verify", {"code":"000111","source":"attacker"})
v=call("verify", {"code":"778899","source":"attacker"})
ok("rate limit kicks in", v.get("HTTP")==429)
v=call("verify", {"code":"778899","source":"test"})
ok("other sources unaffected", v.get("allowed") is True)

# --- keypad ---
call("keypad/settings", {"enabled":True,"action":"unlock","min_interval_seconds":0})
call("lock")
call("keypad/event", {"attempt_state":10,"battery":88})          # first sighting
e=call("keypad/event", {"attempt_state":11})                      # +1
ok("keypad +1 => rejected", e["result"]=="rejected" and e["acted"] is False)
e=call("keypad/event", {"attempt_state":13})                      # +2
ok("keypad +2 => accepted & unlocked", e["result"]=="accepted" and e["acted"] is True)
ok("lock actually opened", call("status")["state"]=="unlocked")
call("lock")
call("keypad/event", {"attempt_state":255})
e=call("keypad/event", {"attempt_state":1})                       # wrap 255->1 = +2
ok("counter wrap 255->1 => accepted", e["result"]=="accepted")
call("lock")
call("keypad/event", {"attempt_state":255})
e=call("keypad/event", {"attempt_state":0})                       # wrap 255->0 = +1
ok("counter wrap 255->0 => rejected", e["result"]=="rejected")

# --- jam handling ---
call("dev/jam", {"enabled":True})
j=call("unlock")
ok("jam detected", j.get("HTTP")==409)
call("dev/jam", {"enabled":False})
ok("state sticky jammed", call("status")["state"]=="jammed")
ok("recovers after clearing jam", call("unlock")["state"]=="unlocked")

ok("events logged", len(call("events?limit=100")["events"])>10)
print("\nALL TESTS PASSED")
