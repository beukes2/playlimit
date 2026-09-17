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
print("FAILURES:", fails)

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
