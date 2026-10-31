"""Prepare explicitly selected public assets; never copies the input tree."""
from pathlib import Path
from PIL import Image, ImageOps
import shutil, json, hashlib

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r'D:\淘宝店铺')
OUT = ROOT / 'docs'
ASSETS = OUT / 'assets'
DOWNLOADS = OUT / 'downloads'
ASSETS.mkdir(parents=True, exist_ok=True)
DOWNLOADS.mkdir(parents=True, exist_ok=True)
images = {
    'logo.webp': ('logo.png', 480),
    'board8.webp': ('8通道BCI/淘宝上架资料_20261007/图片/01_板卡正面_隐藏校徽.png', 1400),
    'board8-back.webp': ('8通道BCI/淘宝上架资料_20261007/图片/06_板卡背面原照.jpg', 1400),
    'board4.webp': ('4通道BCI/3D正面.jpg', 1400),
    'board4-back.webp': ('4通道BCI/3D反面.jpg', 1400),
    'board4-photo.webp': ('4通道BCI/正面俯视图.jpg', 1400),
    'windows.webp': ('8通道BCI/淘宝上架资料_20261007/图片/05_Windows演示界面.jpg', 1600),
    'android.webp': ('8通道BCI/淘宝上架资料_20261007/图片/07_移动端演示A.png', 1000),
    'ios.webp': ('8通道BCI/淘宝上架资料_20261007/图片/08_移动端演示B.png', 1000),
}
for name, (relative, size) in images.items():
    with Image.open(SOURCE / relative) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((size, size))
        im.convert('RGB').save(ASSETS / name, 'WEBP', quality=88, method=6)

files = [
 ('firmware.ino', '8通道BCI/程序/嵌入式程序/ESP32S3_ADS1299_EEG.ino', 'ESP32-S3 采集固件', '固件源码', '当前 BLE 固件 v1.0.0；使用前核对引脚与板卡版本。'),
 ('eeg_host.py', '8通道BCI/程序/上位机程序/Python/eeg_host.py', 'Python 桌面上位机', '软件源码', '8 通道 BLE 接收、波形、数字滤波与 CSV 记录。'),
 ('EEG-Mobile-v1.0.0.apk', '8通道BCI/程序/上位机程序/Android/EEG-Mobile-v1.0.0.apk', 'Android 安装包', '安装包', 'v1.0.0 · Android 8.0 及以上；来自提供的 release 文件。'),
 ('technical-specification.pdf', '8通道BCI/淘宝上架资料_20261007/文档/03_技术规格说明.pdf', '8 通道技术规格说明', 'PDF 文档', '原始技术资料，2026-10-07；部分待确认项已可从当前源码补充。'),
 ('development-guide.pdf', '8通道BCI/淘宝上架资料_20261007/文档/04_开发接口与使用指南.pdf', '开发接口与使用指南', 'PDF 文档', '原始接口核对、开发流程与使用注意。'),
 ('delivery-checklist.pdf', '8通道BCI/淘宝上架资料_20261007/文档/05_定制需求与交付验收.pdf', '定制需求与交付验收', 'PDF 文档', '需求清单与交付验收模板。'),
 ('android-demo.mp4', '8通道BCI/安卓.mp4', 'Android 操作演示', '演示视频', '现有视频素材；界面以对应软件版本为准。'),
 ('ios-demo.mp4', '8通道BCI/IOS.mp4', 'iOS 操作演示', '演示视频', '现有视频素材；不代表源码已完成真机验证或已提供 IPA。'),
]
manifest = []
for name, relative, title, kind, note in files:
    dest = DOWNLOADS / name
    shutil.copyfile(SOURCE / relative, dest)
    manifest.append(dict(file=name, title=title, kind=kind, note=note, source=relative))
(ROOT / 'content' / 'assets.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'Prepared {len(images)} optimized images and {len(files)} selected public files.')
