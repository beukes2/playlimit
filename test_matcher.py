import sys
sys.path.insert(0, r'C:\Users\space\Documents\Default Project\playlimit')
import albion_limiter as al

cases = [
    # (name, cmdline, ancestors, expected, why)
    ("Albion-Online.exe", [], [], True, "albion explicit"),
    ("Albion-Online_BE.exe", [], [], True, "albion BE sidecar"),
    ("SomeAlbionThing.exe", [], [], True, "albion catch-all"),
    ("MinecraftLauncher.exe", [], [], True, "mc launcher"),
    ("Minecraft.Windows.exe", [], [], True, "bedrock"),
    ("javaw.exe", ["-Xmx2G", "-Djava.library.path", "net.minecraft.client.main.Main"], [], True, "mc java"),
    ("javaw.exe", ["-jar", "myapp.jar"], [], False, "random java app NOT counted"),
    ("java.exe", [], ["MinecraftLauncher.exe"], True, "java under mc launcher"),
    ("RobloxPlayerBeta.exe", [], [], True, "roblox"),
    ("FortniteClient-Win64-Shipping.exe", [], [], True, "fortnite"),
    ("GameOverlayUI.exe", [], ["steam.exe"], True, "steam overlay only exists during gameplay"),
    ("steam.exe", [], [], False, "steam client itself"),
    ("steamwebhelper.exe", [], ["steam.exe"], False, "steam helper"),
    ("SteamService.exe", [], [], False, "steam service"),
    ("SomeRandomGame.exe", [], ["steam.exe"], True, "ANY steam-launched game"),
    ("SomeRandomGame.exe", [], ["explorer.exe"], False, "non-steam random exe"),
    ("chrome.exe", [], [], False, "browser not a game"),
    ("notepad.exe", [], [], False, "notepad"),
    ("", [], [], False, "empty"),
]
fails = 0
for name, cmd, anc, exp, why in cases:
    got = al._is_game_process(name, cmd, anc)
    ok = got == exp
    if not ok:
        fails += 1
    print(("PASS" if ok else "FAIL"), f"{name} -> {got} (want {exp}) [{why}]")
print("MATCHER FAILURES:", fails)

# tasklist union pass (elevated-blindness cover)
t="@\"System Idle Process\",\"0\",\"Services\",\"0\",\"8 K\"\n\"Albion-Online.exe\",\"1234\",\"Console\",\"1\",\"100 K\"\n\"notepad.exe\",\"5678\",\"Console\",\"1\",\"10 K\""
hits = al._tasklist_game_hits(t)
t_ok = "albion-online.exe" in hits and "notepad.exe" not in hits
print(("PASS" if t_ok else "FAIL"), f"tasklist hits={sorted(hits)}")
if not t_ok:
    fails += 1
t2 = al._tasklist_game_hits('"javaw.exe","9999","Console","1","200 K"')
t2_ok = len(t2) == 0
print(("PASS" if t2_ok else "FAIL"), f"tasklist ignores bare javaw: {sorted(t2)}")
if not t2_ok:
    fails += 1
print("TOTAL FAILURES:", fails)

# live environment checks
import datetime
base = datetime.date(2026, 9, 7)
for i in range(7):
    d = base + datetime.timedelta(days=i)
    print(d.strftime("%a"), al.get_daily_limit_sec(d) // 60, "min")
print("live running games now:", al.get_running_games())
try:
    import psutil
    steam = [p.info for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == "steam.exe"]
    print("steam.exe running:", len(steam) > 0)
except Exception as e:
    print("psutil err", e)
