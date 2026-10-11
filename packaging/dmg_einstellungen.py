# Layout des DMG-Fensters für dmgbuild (https://dmgbuild.readthedocs.io).
# Positionen aus assets/icons/README.txt: App bei x=180, Programme-Verknüpfung bei x=480, beide y=200.
import os.path

app = defines.get("app", "build/heyAtlas.app")  # noqa: F821 - von dmgbuild bereitgestellt
app_name = os.path.basename(app)

format = "ULMO"  # LZMA-komprimiert, ab macOS 10.15
files = [app]
symlinks = {"Programme": "/Applications"}
icon = "assets/icons/out/VolumeIcon.icns"
background = "assets/icons/out/dmg-background.png"  # dmgbuild nimmt dmg-background@2x.png für Retina dazu

window_rect = ((200, 120), (660, 400))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 112
text_size = 13
icon_locations = {app_name: (180, 200), "Programme": (480, 200)}
