
set -ax
mkdir MyIcon.iconset
# From the original PNG (let's say it's 1024x1024)
sips -z 16 16 alpaca.png --out MyIcon.iconset/icon_16x16.png
sips -z 32 32 alpaca.png --out MyIcon.iconset/icon_16x16@2x.png
sips -z 32 32 alpaca.png --out MyIcon.iconset/icon_32x32.png
sips -z 64 64 alpaca.png --out MyIcon.iconset/icon_32x32@2x.png
sips -z 128 128 alpaca.png --out MyIcon.iconset/icon_128x128.png
sips -z 256 256 alpaca.png --out MyIcon.iconset/icon_128x128@2x.png
sips -z 256 256 alpaca.png --out MyIcon.iconset/icon_256x256.png
sips -z 512 512 alpaca.png --out MyIcon.iconset/icon_256x256@2x.png
sips -z 512 512 alpaca.png --out MyIcon.iconset/icon_512x512.png
sips -z 1024 1024 alpaca.png --out MyIcon.iconset/icon_512x512@2x.png

iconutil -c icns MyIcon.iconset
mv MyIcon.icns alpaca.icns
