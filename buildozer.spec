[app]
title = VRChat Group Ban
package.name = vrchatgroupban
package.domain = org.vrcban
source.dir = .
source.include_exts = py,png
version = 1.0

icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/icon.png
android.presplash_color = #08090C

requirements = python3,kivy==2.3.0,requests==2.31.0,urllib3==1.26.18,chardet==5.2.0,idna,certifi

orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

# Bewaehrte Toolchain-Version festnageln (neueres p4a nutzt Python 3.14,
# mit dem Kivy 2.3.0 nicht baut)
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
