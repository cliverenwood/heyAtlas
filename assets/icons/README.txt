Hey Atlas – Icon-Set
====================
out/HeyAtlas.icns            App-Icon (Dock, Finder) -> in Contents/Resources der .app, in Info.plist: CFBundleIconFile = HeyAtlas
out/HeyAtlas.iconset/        Alle PNG-Größen; auf dem Mac neu bauen mit:  iconutil -c icns HeyAtlas.iconset
out/HeyAtlas-AppIcon-1024.png  Vorschau / Quelle
out/MenuBar/*Template*.png   Menüleisten-Icons (Ruhe + "hört zu"), je @1x/@2x/@3x.
                             Der Dateiname muss auf "Template" enden, damit macOS sie an Hell/Dunkel anpasst
                             (NSImage: isTemplate = true).
out/VolumeIcon.icns          Symbol des gemounteten DMG (als .VolumeIcon.icns ins Image kopieren)
out/dmg-background.png/@2x   Fensterhintergrund 660x400 (App-Symbol bei x=180,y=200; Programme-Alias bei x=480,y=200)
src/*.svg                    Vektorquellen;  build.py rendert alles neu (python3 build.py, braucht cairosvg + Pillow)
