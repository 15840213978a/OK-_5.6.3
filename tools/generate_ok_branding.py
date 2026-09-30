#!/usr/bin/env python3
"""Regenerate OK launcher resources: pip install Pillow PyMuPDF fonttools.

All artwork is code-native vector geometry. Android XML and legacy bitmaps
share the same paths; Chinese banner lettering is outlined, not device text.
"""
from pathlib import Path
from io import BytesIO
import xml.etree.ElementTree as ET
import fitz
from PIL import Image
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'app/src/main'
TV = ROOT / 'app/src/leanback/res'
ANDROID = 'http://schemas.android.com/apk/res/android'
INK = '#11243A'
TEAL = '#22D3B0'
WHITE = '#FFFFFF'


def rounded(x, y, w, h, r):
    return (f'M{x+r},{y} H{x+w-r} Q{x+w},{y} {x+w},{y+r} '
            f'V{y+h-r} Q{x+w},{y+h} {x+w-r},{y+h} H{x+r} '
            f'Q{x},{y+h} {x},{y+h-r} V{y+r} Q{x},{y} {x+r},{y} Z')


# The complete mark stays within the adaptive icon's central safe circle.
MARK = [
    (TEAL, rounded(27, 34, 54, 37, 7) + ' ' + rounded(31, 38, 46, 29, 3)),
    (WHITE, rounded(37, 44, 16, 17, 5) + ' ' + rounded(41, 48, 8, 9, 2)),
    (WHITE, 'M58,44 H62 V50 L68,44 H74 L66,52 L74,61 H68 L62,55 V61 H58 Z'),
    (TEAL, 'M48,71 H60 V75 H65 V79 H43 V75 H48 Z'),
]


def vector(paths, width=108, height=108, group=None):
    body = ''.join(f'    <path android:fillColor="{c}" android:fillType="evenOdd" '
                   f'android:pathData="{d}" />\n' for c, d in paths)
    if group:
        body = f'    <group {group}>\n{body}    </group>\n'
    return (f'<?xml version="1.0" encoding="utf-8"?>\n'
            f'<vector xmlns:android="{ANDROID}" android:width="{width}dp" '
            f'android:height="{height}dp" android:viewportWidth="{width}" '
            f'android:viewportHeight="{height}">\n{body}</vector>\n')


def svg(paths, width=108, height=108, transform=''):
    body = ''.join(f'<path fill="{c}" fill-rule="evenodd" d="{d}"/>' for c, d in paths)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}"><g transform="{transform}">{body}</g></svg>')


def raster(source, size):
    with fitz.open(stream=source.encode(), filetype='svg') as doc:
        pdf = doc.convert_to_pdf()
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(size[0] * 4 / page.rect.width,
                                               size[1] * 4 / page.rect.height), alpha=True)
        return Image.open(BytesIO(pix.tobytes('png'))).resize(size, Image.Resampling.LANCZOS)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='utf-8')


def main():
    write(MAIN / 'res/drawable/ic_launcher_foreground.xml', vector(MARK))
    write(MAIN / 'res/drawable/ic_launcher_monochrome.xml', vector([(WHITE, d) for _, d in MARK]))
    write(MAIN / 'res/values/ok_launcher_colors.xml',
          '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
          f'    <color name="ok_launcher_background">{INK}</color>\n</resources>\n')
    for p in [* (MAIN / 'res/mipmap-anydpi-v26').glob('ic_launcher*.xml'),
              TV / 'mipmap-anydpi-v26/ic_banner.xml']:
        write(p, p.read_text().replace('@color/white', '@color/ok_launcher_background'))

    # Legacy launchers do not apply adaptive masks. Export each density explicitly.
    square = [(INK, rounded(3, 3, 102, 102, 23))] + MARK
    circle = [(INK, 'M54,1 A53,53 0 1,1 54,107 A53,53 0 1,1 54,1 Z')] + MARK
    for density, size in [('mdpi', 48), ('hdpi', 72), ('xhdpi', 96), ('xxhdpi', 144), ('xxxhdpi', 192)]:
        folder = MAIN / f'res/mipmap-{density}'
        raster(svg(square), (size, size)).save(folder / 'ic_launcher.png')
        raster(svg(circle), (size, size)).save(folder / 'ic_launcher_round.webp', lossless=True)
    raster(svg(square), (512, 512)).save(MAIN / 'ic_launcher-playstore.png')

    # Keep splash animation target names consistent with the new vector.
    write(MAIN / 'res/drawable/ic_splash_logo.xml', vector(MARK, group=
          'android:name="ok_logo_motion" android:pivotX="54" android:pivotY="54"'))
    write(MAIN / 'res/drawable-v31/ic_splash_logo_animated.xml', f'''<?xml version="1.0" encoding="utf-8"?>
<animated-vector xmlns:android="{ANDROID}"
    xmlns:aapt="http://schemas.android.com/aapt" android:drawable="@drawable/ic_splash_logo">
    <target android:name="ok_logo_motion">
        <aapt:attr name="android:animation">
            <set android:ordering="together">
                <objectAnimator android:propertyName="scaleX" android:valueFrom="0.9" android:valueTo="1" android:valueType="floatType" android:duration="300" android:interpolator="@android:interpolator/fast_out_slow_in" />
                <objectAnimator android:propertyName="scaleY" android:valueFrom="0.9" android:valueTo="1" android:valueType="floatType" android:duration="300" android:interpolator="@android:interpolator/fast_out_slow_in" />
            </set>
        </aapt:attr>
    </target>
</animated-vector>
''')

    # TV banner: outlined Chinese lettering remains legible without installed fonts.
    font = TTFont(BytesIO(fitz.Font('china-s').buffer))
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    pen = SVGPathPen(glyphs)
    x = 122
    for char in 'OK影视':
        glyph = glyphs[cmap[ord(char)]]
        scale = 30 / font['head'].unitsPerEm
        glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, x, 101)))
        x += glyph.width * scale + 2
    # Shift the icon next to the label, preserving a central safe area for TV masks.
    # A vector group handles the icon transform; SVG is also used for the bitmap.
    lettering = [(WHITE, pen.getCommands())]
    banner_xml = vector(lettering, 320, 180).replace('</vector>',
        '    <group android:translateX="26" android:translateY="35">\n' +
        ''.join(f'        <path android:fillColor="{c}" android:fillType="evenOdd" android:pathData="{d}" />\n' for c, d in MARK) +
        '    </group>\n</vector>')
    write(TV / 'drawable/ic_banner_foreground.xml', banner_xml)
    banner_svg = svg([(INK, 'M0,0 H320 V180 H0 Z')] + lettering, 320, 180).replace('</svg>',
        '<g transform="translate(26 35)">' + ''.join(f'<path fill="{c}" fill-rule="evenodd" d="{d}"/>' for c, d in MARK) + '</g></svg>')
    raster(banner_svg, (320, 180)).save(TV / 'drawable/ic_banner.png')
    write(TV / 'mipmap/ic_banner.xml', f'<?xml version="1.0" encoding="utf-8"?>\n'
          f'<bitmap xmlns:android="{ANDROID}" android:src="@drawable/ic_banner" android:gravity="fill" />\n')

    # Preview is temporary, not an Android resource.
    previews = Image.new('RGB', (768, 320), '#E8EDF3')
    for i, source in enumerate([svg(square), svg(circle)]):
        im = raster(source, (192, 192))
        previews.paste(im, (20 + i * 216, 56), im)
    banner = raster(banner_svg, (320, 180))
    previews.paste(banner, (448, 66), banner)
    previews.save(ROOT.parent / 'ok-launcher-preview.png')
    for path in [*MAIN.glob('res/**/ic_launcher*.xml'), MAIN / 'res/drawable/ic_splash_logo.xml',
                 MAIN / 'res/drawable-v31/ic_splash_logo_animated.xml', *TV.glob('**/ic_banner*.xml')]:
        ET.parse(path)
    print('Generated launcher densities, adaptive/monochrome icons, splash and TV banners; XML parsed.')


if __name__ == '__main__':
    main()
