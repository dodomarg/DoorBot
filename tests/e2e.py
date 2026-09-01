import json, urllib.request, time, sys, threading

RUN = str(int(time.time()))            # per-run source ids keep the rate limiter clean
SRC, ATTACKER = "test-" + RUN, "attacker-" + RUN
B="http://localhost:8099/api/"
POSTS = ("lock","unlock","stop","verify","dev/jam","keypad/event","keypad/settings",
         "keypad/credentials",
         "open","dev/slip","dev/offline",
         "calibration","calibration/torque","calibration/goto","calibration/jog",
         "calibration/capture","calibration/reset","calibration/swap","codes")
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
call("dev/jam", {"enabled": False})   # in case an earlier run aborted mid-jam
call("dev/offline", {"enabled": False})

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

# --- multi-turn travel ---
# A euro cylinder often needs more than one full revolution. In multi-turn mode
# the servo accepts goals outside a single 0..4095 turn.
call("calibration/reset")   # an uncalibrated lock may travel its whole range
s=call("calibration", {"multi_turn": True})
ok("multi-turn enabled", s["calibration"]["multi_turn"] is True)
s=call("calibration/goto", {"position": 6000}); time.sleep(0.6)
s=call("status", method="GET")
ok("travelled past one full turn", s["servo"]["position"] > 4095)
ok("turns reported", s["servo"]["turns"] > 1.0)
# The 12-bit trap: a multi-turn step count is not an angle. Degrees must stay
# inside one revolution and the revolution count must be carried separately.
ok("degrees stay within one revolution", 0 <= s["servo"]["degrees"] < 360)
ok("degrees are the remainder, not the total",
   abs(s["servo"]["degrees"] - (s["servo"]["position"] % 4096) * 360 / 4096) < 0.2)

s=call("calibration/goto", {"position": -2500}); time.sleep(0.9)
s=call("status", method="GET")
ok("negative multi-turn position", s["servo"]["position"] < 0)
ok("negative positions still report a sane angle", 0 <= s["servo"]["degrees"] < 360)
ok("negative turns are signed", s["servo"]["turns"] < 0)

# Single-turn mode must clamp back into one revolution.
call("calibration", {"multi_turn": False})
call("calibration/goto", {"position": 9000}); time.sleep(0.8)
s=call("status", method="GET")
ok("single-turn clamps to one revolution", s["servo"]["position"] <= 4095)

# --- holding verification ---
# The contract: torque is on while a move runs or a bounded hold runs, and at
# no other time. A plain move must therefore leave the servo released.
call("calibration", {"multi_turn": False})
call("calibration/goto", {"position": 2000}); time.sleep(0.5)
s=call("status", method="GET")
ok("move result reported", s["servo"]["move_result"] == "arrived")
ok("a finished move leaves the servo released", s["servo"]["torque"] is False)
ok("and therefore not holding", s["servo"]["holding"] is False)

# During a granted hold, and only then, it really is holding. Capture a fresh
# calibration first: a hold point is only accepted past the unlocked end in the
# unlocking direction, so it has to be derived from the real end points.
call("calibration/goto", {"position": 2400}); time.sleep(0.4)
call("calibration/capture", {"which":"locked"})
call("calibration/goto", {"position": 1500}); time.sleep(0.4)
call("calibration/capture", {"which":"unlocked"})
_cal=call("status", method="GET")["calibration"]
_hold=int(_cal["unlocked_position"] - (_cal["locked_position"] - _cal["unlocked_position"]) * 0.4)
_r=call("calibration", {"hold_position": _hold, "hold_seconds": 3})
ok("a hold point past unlocked is accepted for the holding test",
   _r.get("HTTP") is None and int(_r["calibration"]["hold_seconds"])==3)
_hres={}
_ht=threading.Thread(target=lambda: _hres.update(r=call("open")))
_ht.start(); time.sleep(1.5)
s=call("status", method="GET")
ok("servo reports holding during a bounded hold", s["servo"]["holding"] is True)
ok("torque is on during a bounded hold", s["servo"]["torque"] is True)
_ht.join(); time.sleep(0.5)
s=call("status", method="GET")
ok("released once the hold window ends", s["servo"]["torque"] is False)
call("calibration", {"hold_seconds": 0})

# --- hold open, and the fact that it is bounded ---
# Re-establish a known calibration, then park a hold point past the unlocked end.
call("calibration/goto", {"position": 2400}); time.sleep(0.4)
call("calibration/capture", {"which":"locked"})
call("calibration/goto", {"position": 1500}); time.sleep(0.4)
call("calibration/capture", {"which":"unlocked"})
s=call("calibration", {"hold_position": 1100, "hold_seconds": 1, "overshoot": 0})
ok("hold settings saved", s["calibration"]["hold_seconds"]==1)

t0=time.time()
s=call("open")
elapsed=time.time()-t0
ok("open held the latch then returned", elapsed >= 1.0)
ok("ended unlocked", s["state"]=="unlocked")
ok("back at the unlocked point",
   abs(s["servo"]["position"] - s["calibration"]["unlocked_position"]) <= 25)
evts=[e["kind"] for e in call("events", method="GET").get("events", [])]
ok("hold logged", "hold_open" in evts)

# The hold must end on its own deadline, not because someone asked it to. This
# is the property that keeps a crashed add-on from clamping the door.
call("lock"); time.sleep(0.6)
call("calibration", {"hold_seconds": 1})
call("open")
time.sleep(0.3)
s=call("status", method="GET")
ok("the servo is not left energised after a hold", s["servo"]["torque"] is False)

# Torque may never be held open-endedly, whoever asks. This is the old wizard
# "Hold position" button; the firmware refuses it too, independently.
r=call("calibration/torque", {"enabled": True})
ok("an indefinite hold is refused", r.get("HTTP")==409)
ok("the refusal explains itself", "time limit" in str(r.get("error","")).lower())
s=call("status", method="GET")
ok("and the servo stayed released", s["servo"]["torque"] is False)
ok("releasing is still allowed", call("calibration/torque", {"enabled": False}).get("HTTP") is None)

# A hold longer than the ceiling is clamped rather than honoured.
s=call("calibration", {"hold_seconds": 999})
ok("an over-long hold is refused or clamped",
   s.get("HTTP")==400 or int(s.get("calibration",{}).get("hold_seconds",0)) <= 60)

# A slip during the hold must be reported, and the latch must still be released.
call("calibration", {"hold_seconds": 2})
call("lock"); time.sleep(0.6)
call("dev/slip", {"enabled": True})
s=call("open")
ok("slip reported", "hold_slipped" in
   [e["kind"] for e in call("events", method="GET").get("events", [])])
ok("latch released after a slip",
   abs(s["servo"]["position"] - s["calibration"]["unlocked_position"]) <= 25)
ok("still ends unlocked after a slip", s["state"]=="unlocked")

# Fail secure: if the hold move itself jams, the latch must not stay retracted.
call("lock"); time.sleep(0.6)
call("dev/jam", {"enabled": True})
j=call("open")
call("dev/jam", {"enabled": False})
ok("a jam during open is reported", j.get("HTTP")==409)
time.sleep(0.6)
s=call("status", method="GET")
ok("not left at the hold position",
   abs(s["servo"]["position"] - s["calibration"]["hold_position"]) > 25)

# The status API must stay responsive while a hold is running, and a lock
# issued mid-hold must win rather than being undone when the hold expires.
call("calibration", {"hold_seconds": 3})
call("lock"); time.sleep(0.6)
_res={}
_t=threading.Thread(target=lambda: _res.update(open=call("open")))
_t.start(); time.sleep(1.2)
t0=time.time(); call("status", method="GET"); poll=time.time()-t0
ok("status stays responsive during a hold", poll < 0.5)
call("lock"); _t.join(); time.sleep(0.8)
s=call("status", method="GET")
ok("lock during a hold wins", s["state"]=="locked")
call("calibration", {"hold_seconds": 0})

# With no hold configured, open() is just an unlock and returns promptly.
call("calibration", {"hold_seconds": 0})
call("lock"); time.sleep(0.4)
t0=time.time(); baseline=call("unlock"); plain=time.time()-t0
call("lock"); time.sleep(0.4)
t0=time.time(); s=call("open"); elapsed=time.time()-t0
# Same work as an unlock: no extra hold, no second trip back.
ok("open without hold is a plain unlock",
   s["state"]=="unlocked" and baseline["state"]=="unlocked" and elapsed < plain + 0.5)

# --- jam handling ---
call("lock"); time.sleep(0.6)         # a jam only shows up on a move that has work to do
call("dev/jam", {"enabled":True})
j=call("unlock")
ok("jam detected", j.get("HTTP")==409)
call("dev/jam", {"enabled":False})
ok("state sticky jammed", call("status")["state"]=="jammed")
ok("recovers after clearing jam", call("unlock")["state"]=="unlocked")

# --- servo offline: every path that moves the motor must refuse and say why ---
# This is the class of bug the suite used to be blind to: a move against a servo
# that is not there used to report success.
call("unlock"); time.sleep(0.6)
call("dev/offline", {"enabled": True})
s=call("status")
ok("status reports servo offline", s["servo"]["online"] is False)
ok("state is unknown while offline", s["state"]=="unknown")
for _name, _p, _b in (("lock","lock",None), ("unlock","unlock",None),
                      ("open","open",None),
                      ("jog","calibration/jog",{"delta":25}),
                      ("goto","calibration/goto",{"position":2400}),
                      ("torque","calibration/torque",{"enabled":True}),
                      ("capture","calibration/capture",{"which":"locked"})):
    r=call(_p, _b)
    ok("offline %s is rejected" % _name, r.get("HTTP")==409)
    ok("offline %s explains why" % _name, "not responding" in r.get("error","").lower())
before=call("status")["servo"]["position"]
call("dev/offline", {"enabled": False})
ok("recovers when the servo comes back", call("status")["servo"]["online"] is True)
ok("offline moves never moved anything", call("status")["servo"]["position"]==before)
ok("offline logged", any(e["kind"]=="offline" for e in call("events?limit=100")["events"]))
ok("unlock works again after recovery", call("unlock")["state"]=="unlocked")


# The add-on manifest and the code used to carry different version numbers, so
# the UI reported a version that had not existed for two releases.
import re as _re, pathlib as _pl
_manifest = _pl.Path(__file__).resolve().parent.parent / "doorbot" / "config.yaml"
_declared = _re.search(r'^version:\s*"?([^"\n]+)"?', _manifest.read_text(), _re.M).group(1).strip()
ok("code version matches the add-on manifest", call("info")["version"] == _declared)


# --- direction of rotation ---
call("calibration/reset")
call("calibration/torque", {"enabled": True})
call("calibration/goto", {"position": 1500}); time.sleep(0.4)
call("calibration/capture", {"which": "unlocked"})
call("calibration/goto", {"position": 2400}); time.sleep(0.4)
s=call("calibration/capture", {"which": "locked"})
# The mock's simulated hard stops can clamp a capture, so compare against what
# was actually captured rather than against the position we asked for.
_lk, _ul = s["calibration"]["locked_position"], s["calibration"]["unlocked_position"]
ok("direction derived from the captured points", s["direction"]["sign"]==1 and _lk>_ul)
ok("locking reads as clockwise", s["direction"]["locking"]=="clockwise")
ok("unlocking is the opposite", s["direction"]["unlocking"]=="counter-clockwise")

# A hold-open point must sit past unlocked, away from locked.
_wrong, _right = _ul + 100, _ul - 200      # locking is +ve here, so past unlocked is -ve
r=call("calibration", {"hold_seconds": 5, "hold_position": _wrong})
ok("hold point on the locked side is rejected", r.get("HTTP")==409)
ok("rejection names the direction", "locked side" in r.get("error",""))
ok("rejected save did not persist",
   call("calibration", method="GET")["hold_position"]!=_wrong)
s=call("calibration", {"hold_seconds": 5, "hold_position": _right})
ok("hold point past unlocked is accepted", s["calibration"]["hold_position"]==_right)
ok("hold point reported valid", s["direction"]["hold_valid"] is True)

# Swapping reverses travel and carries the hold point across with it.
s=call("calibration/swap")
ok("swap reverses the direction", s["direction"]["sign"]==-1)
ok("swap exchanges the end points",
   s["calibration"]["locked_position"]==_ul and s["calibration"]["unlocked_position"]==_lk)
ok("swap keeps the hold point the same distance past unlocked",
   s["calibration"]["hold_position"]-_lk == _ul-_right)
ok("hold point still valid after swap", s["direction"]["hold_valid"] is True)
ok("lock still works after swapping", call("lock")["state"]=="locked")
ok("unlock still works after swapping", call("unlock")["state"]=="unlocked")

# Re-capturing so the direction flips must retire a now-wrong-side hold point.
call("calibration/goto", {"position": _lk + 300}); time.sleep(0.6)
s=call("calibration/capture", {"which": "locked"})
ok("direction flipped back by recapture", s["direction"]["sign"]==1)
ok("stranded hold point was retired",
   s["calibration"]["hold_position"]==s["calibration"]["unlocked_position"])
ok("hold point valid after recapture", s["direction"]["hold_valid"] is True)

# Put the rig back the way the rest of the suite expects it.
call("calibration", {"hold_seconds": 0})
call("calibration/reset")
call("calibration/goto", {"position": 2400}); time.sleep(0.4)
call("calibration/capture", {"which":"locked"})
call("calibration/goto", {"position": 1500}); time.sleep(0.4)
call("calibration/capture", {"which":"unlocked"})

# invert must actually change behaviour rather than just being stored. Jog from
# the midpoint so the simulated hard stops cannot clamp the move either way.
_c=call("calibration", method="GET")
_mid=(_c["locked_position"]+_c["unlocked_position"])//2
def _jog_delta():
    call("calibration/goto", {"position": _mid}); time.sleep(0.5)
    _b=call("status")["servo"]["position"]
    call("calibration/jog", {"delta": 100}); time.sleep(0.4)
    return call("status")["servo"]["position"]-_b
call("calibration", {"invert": False})
_plain=_jog_delta()
call("calibration", {"invert": True})
_inv=_jog_delta()
ok("invert reverses jog direction", _plain>0 and _inv<0)
call("calibration", {"invert": False})
ok("direction survives a reload", call("status")["direction"]["sign"]==1)


ok("events logged", len(call("events?limit=100")["events"])>10)
print("\nALL TESTS PASSED")
