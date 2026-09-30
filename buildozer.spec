[app]
title = VRChat Group Ban
package.name = vrchatgroupban
package.domain = org.vrcban
source.dir = .
source.include_exts = py
version = 1.0

requirements = python3,kivy==2.3.0,requests==2.31.0,urllib3==1.26.18,chardet==5.2.0,idna,certifi

orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
