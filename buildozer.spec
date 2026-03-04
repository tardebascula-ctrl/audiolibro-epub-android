[app]
title = AudioLibro
package.name = audiolibro
package.domain = org.test
source.dir = .
source.include_exts = py,kv,png,jpg,ttf,txt

version = 0.1

requirements = python3,kivy

orientation = portrait

android.api = 33
android.minapi = 23
android.ndk = 25b
android.build_tools_version = 34.0.0

[buildozer]
android.accept_sdk_license = True
android.skip_update = True
