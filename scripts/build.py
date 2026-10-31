"""Generate a portable, buildless documentation site from reviewed content."""
from pathlib import Path
import json, re, html, hashlib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs'
BRAND = '智链脑机工坊'
DATE = '2026-10-31'
ORDER = ['overview','quickstart','bci4','bci8','firmware','protocol','python','android','ios','downloads','faq','customization','sources']
GROUPS = [('开始使用',['overview','quickstart']),('产品文档',['bci4','bci8']),('开发与软件',['firmware','protocol','python','android','ios']),('资料与支持',['downloads','faq','customization','sources'])]
esc = html.escape

def plain(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', value))).strip()

def figure(path, caption, cls=''):
    return f'<figure class="{cls}"><a href="assets/{path}" target="_blank" rel="noopener"><img src="assets/{path}" alt="{esc(caption)}" loading="lazy"></a><figcaption>{caption} · 点击查看大图</figcaption></figure>'

def page(id, title, group, description, sections, sources=None):
    return dict(id=id,title=title,group=group,description=description,sections=sections,sources=sources or [])

def section(id,title,body):
    return dict(id=id,title=title,html=body)

pages = []
for name in ['four-channel.json','technical.json','mobile.json']:
    file = ROOT / 'content' / name
    if file.exists(): pages.extend(json.loads(file.read_text(encoding='utf-8-sig')))

manifest = json.loads((ROOT / 'content' / 'assets.json').read_text(encoding='utf-8'))
for name,title,note in [('android-source.zip','Android 客户端源码','精简工程，已移除本地签名凭据、构建缓存和测试数据。构建说明见归档内 README。'),('ios-source.zip','iOS 客户端源码','React Native / Expo 工程；需要自行配置开发签名，未提供已签名 IPA。')]:
    if (OUT / 'downloads' / name).exists(): manifest.append(dict(file=name,title=title,kind='软件源码',note=note,source='已筛选的移动端工程'))
for item in manifest:
    path = OUT / 'downloads' / item['file']
    item['bytes'] = path.stat().st_size
    item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()

def size(n):
    return f'{n/1024/1024:.2f} MB' if n >= 1024**2 else f'{n/1024:.1f} KB'

download_sections = []
for key,title in [('software','固件与软件'),('documents','技术资料'),('videos','演示视频')]:
    entries = [x for x in manifest if (x['kind'] == '演示视频' if key == 'videos' else x['kind'] == 'PDF 文档' if key == 'documents' else x['kind'] not in ['演示视频','PDF 文档'])]
    body = ''
    for x in entries:
        body += f'<div class="download-card"><span class="tag">{x["kind"]}</span><h3>{x["title"]}</h3><p>{x["note"]}</p><div class="file-meta">{x["file"]} · {size(x["bytes"])}</div><a class="btn" href="downloads/{x["file"]}" download>下载文件 ↓</a><details><summary>SHA-256 文件校验</summary><code>{x["sha256"]}</code></details></div>'
    download_sections.append(section(key,title,body))
download_sections.append(section('versions','下载与版本说明','<p>下载文件来自所提供资料的指定版本。页面中的代码行为与硬件性能测试是两类信息，请使用与板卡相匹配的固件和上位机。现有软件素材包含第三方依赖；授权、修改和再分发范围以原始许可及交付约定为准。</p><p>4 通道原始参考压缩包未纳入本站。可先阅读 <a href="bci4.html">4 通道文档</a>，确认需要的工程及适用版本。</p><p><a href="downloads/manifest.json" download>下载完整文件校验清单（JSON）</a></p>'))
pages.append(page('downloads','资料下载','资料与支持','获取固件、上位机源码、Android 安装包、技术说明和演示视频。每个下载项均附文件大小与 SHA-256 校验。',download_sections,['当前资料目录中的指定技术文件；详见各文档页面的来源记录。']))
pages.insert(0,page('overview','文档导航','开始使用','从确认产品版本开始，找到对应的硬件、采集固件、数据协议和上位机文档。',[
 section('path','选择你的阅读路径','<table><thead><tr><th>你的任务</th><th>推荐入口</th><th>接下来</th></tr></thead><tbody><tr><td>首次拿到板卡</td><td><a href="quickstart.html">快速开始</a></td><td>核对型号、供电和软件版本，完成台架调试。</td></tr><tr><td>了解产品与接口</td><td><a href="bci4.html">4 通道 BCI</a> / <a href="bci8.html">8 通道 BCI</a></td><td>区分实物、设计图与参考工程的适用范围。</td></tr><tr><td>编写采集程序</td><td><a href="protocol.html">BLE 数据协议</a></td><td>数据帧、控制命令、CRC 和单位换算。</td></tr><tr><td>运行上位机</td><td><a href="python.html">Python</a> / <a href="android.html">Android</a> / <a href="ios.html">iOS</a></td><td>按平台安装、连接、记录和导出。</td></tr></tbody></table>'),
 section('matrix','产品与资料对应关系','<table><thead><tr><th>系列</th><th>已提供资料</th><th>阅读边界</th></tr></thead><tbody><tr><td>4 通道 BCI</td><td>BCI V1.1 图片、3D 图与 ADS1299 参考资料包</td><td>附带参考模块含 8 通道 STM32 工程，不能直接当作 4 通道交付板卡的参数。</td></tr><tr><td>8 通道 BCI</td><td>ESP32-S3 + ADS1299、BLE 固件、Python / Android / iOS 客户端</td><td>源码功能按当前文件整理；引脚、供电、连接器和性能需对应硬件版本确认。</td></tr></tbody></table>'),
 section('software','软件平台','<table><thead><tr><th>平台</th><th>资料状态</th><th>入口</th></tr></thead><tbody><tr><td>Windows / Python</td><td>Python 源码、依赖安装说明、演示截图</td><td><a href="python.html">桌面上位机指南 →</a></td></tr><tr><td>Android</td><td>v1.0.0 APK 与精简源码</td><td><a href="android.html">Android 使用指南 →</a></td></tr><tr><td>iOS</td><td>Xcode 工程源码与演示素材；无已签名 IPA</td><td><a href="ios.html">iOS 开发指南 →</a></td></tr></tbody></table>'),
 section('scope','文档适用范围','<div class="callout"><strong>面向教学与研发</strong><p>首次调试使用信号源或模拟器。现有资料不足以确认人体连接所需的输入保护与隔离；完整系统完成相应评估前，不直接连接人体。本站不提供诊断、治疗或临床使用说明。</p></div><p>文档按提供的材料与当前源码整理，未将源文件中待确认的指标改写为实测结论。查看 <a href="sources.html">资料来源与版本</a>，可以了解每类信息的依据。</p>')
]))
pages.append(page('sources','资料来源与版本','资料与支持','了解产品描述、代码行为和演示素材的来源，保持硬件、固件与客户端版本一致。',[
 section('basis','本次整理依据',f'<p>文档整理日期：<strong>{DATE}</strong>。主要依据为提供的 4 通道资料目录、8 通道技术文档（2026-10-07）及同目录当前程序。</p><table><thead><tr><th>资料</th><th>用途</th></tr></thead><tbody><tr><td>4 通道图片与样图</td><td>外观、板卡标识和资料分类。</td></tr><tr><td>8 通道技术规格、开发接口、定制验收</td><td>产品定位、开发流程及待确认项。</td></tr><tr><td>ESP32-S3 固件 v1.0.0</td><td>当前 BLE 服务、命令、帧结构与代码默认配置。</td></tr><tr><td>Python / Android / iOS 源码与 README</td><td>客户端安装方式、界面选项、处理流程和导出格式。</td></tr><tr><td>原始截图与视频</td><td>界面演示，不能替代整板性能报告。</td></tr></tbody></table>'),
 section('difference','如何理解不同证据','<ul><li><strong>资料说明：</strong>用于了解产品定位和交付范围。</li><li><strong>源码实现：</strong>说明当前文件定义的行为，不等于所有出货版本均具备同样功能。</li><li><strong>界面或视频：</strong>说明该素材展示的操作，不据此推导噪声、精度、延迟和稳定吞吐。</li><li><strong>芯片能力：</strong>应查阅器件原厂资料，整板指标还取决于设计、固件与测试条件。</li></ul>'),
 section('tracking','联调时记录版本','<p>建议在每次记录中保存板卡版本、固件版本、客户端版本、采样率、增益、输入模式和滤波参数。发生问题时，先对照 <a href="faq.html">常见问题</a>，再提供复现步骤及经过必要去标识处理的日志。</p>'),
 section('reading','延伸阅读','<ul><li><a href="https://www.ti.com/product/ADS1299" target="_blank" rel="noopener">Texas Instruments · ADS1299 官方资料</a></li><li><a href="https://www.espressif.com/en/products/socs/esp32-s3" target="_blank" rel="noopener">Espressif · ESP32-S3 官方资料</a></li></ul><p>本站的信息架构参考 <a href="https://www.nologo.tech/" target="_blank" rel="noopener">nologo.tech</a> 的产品分类与文档阅读方式；内容、图片和品牌来自提供的资料。</p>')
]))

for p in pages:
    if p['id'] == 'bci4': p['sections'].insert(1,section('gallery','板卡外观','<div class="image-pair">'+figure('board4.webp','BCI V1.1 正面 3D 图')+figure('board4-back.webp','BCI V1.1 背面 3D 图')+'</div>'+figure('board4-photo.webp','4 通道资料中的板卡实物正面')))
    if p['id'] == 'bci8': p['sections'].insert(1,section('gallery','板卡外观','<div class="image-pair">'+figure('board8.webp','8 通道板卡正面')+figure('board8-back.webp','8 通道板卡背面')+'</div>'))
    if p['id'] == 'python': p['sections'].insert(0,section('interface','界面预览',figure('windows.webp','Windows 上位机演示截图；截图状态不等于实测结论')))
    if p['id'] in ('android','ios'):
        name = 'Android' if p['id']=='android' else 'iOS'
        p['sections'].append(section('video-demo','操作演示',f'<video controls preload="none" playsinline aria-label="{name} 操作演示"><source src="downloads/{p["id"]}-demo.mp4" type="video/mp4">浏览器不支持视频播放，请下载文件查看。</video><p>原始演示素材，功能与界面以对应软件版本为准。<a href="downloads/{p["id"]}-demo.mp4" download>下载视频</a></p>'))

by_id = {p['id']:p for p in pages}
pages = [by_id[x] for x in ORDER if x in by_id]

def nav(active):
    links = [('index','首页'),('overview','产品文档'),('downloads','资料下载'),('customization','定制与支持')]
    out=''
    for id,label in links:
        selected = active == id or (id == 'overview' and active not in ('index','downloads','customization'))
        out += f'<a href="{id}.html"'+(' aria-current="page"' if selected else '')+f'>{label}</a>'
    return out

def base(title, description, body, active):
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>{esc(title)} · {BRAND}</title><meta name="description" content="{esc(description)}"><link rel="icon" href="assets/logo.webp"><link rel="stylesheet" href="assets/style.css"><script src="assets/search-index.js" defer></script><script src="assets/app.js" defer></script></head>
<body><a class="skip" href="#main">跳转到正文</a><header class="site-header"><div class="header-inner"><a class="brand" href="index.html"><img src="assets/logo.webp" alt="" width="42" height="42"><span>{BRAND}<small>BCI DEVELOPMENT DOCS</small></span></a><nav class="main-nav" id="main-nav" aria-label="主导航">{nav(active)}</nav><div class="header-tools"><button class="search-trigger" data-search aria-label="搜索文档"><span aria-hidden="true">⌕</span> 搜索文档 <kbd>Ctrl K</kbd></button><button class="icon-btn" data-theme-toggle aria-label="切换深色模式">◐</button><button class="menu-toggle" aria-controls="main-nav" aria-expanded="false" aria-label="打开导航菜单">☰</button></div></div></header>
{body}
<footer class="site-footer"><div class="wrap footer-inner"><div>{BRAND} · 可穿戴硬件与脑机接口开发</div><div><a href="sources.html">资料来源与版本</a><span>文档整理 · {DATE}</span></div></div></footer><dialog class="search-dialog" aria-label="搜索文档"><div class="search-head"><input type="search" placeholder="搜索产品、协议、安装或参数…" aria-label="搜索关键词" autocomplete="off"><button data-close aria-label="关闭搜索">Esc</button></div><div class="search-results" aria-live="polite"></div><div class="search-footer">↑ ↓ 选择 · Enter 打开 · Esc 关闭</div></dialog><button class="to-top" aria-label="返回顶部">↑</button><noscript><div class="no-js">正文与下载可直接使用。启用 JavaScript 可使用全文搜索、主题切换和代码复制。</div></noscript></body></html>'''

def sidebar(active):
    out='<aside class="sidebar" id="doc-sidebar" aria-label="文档目录">'
    for group, ids in GROUPS:
        out+=f'<div class="nav-heading">{group}</div>'
        for id in ids:
            if id in by_id: out+=f'<a href="{id}.html"'+(' aria-current="page"' if active==id else '')+f'>{by_id[id]["title"]}</a>'
    return out+'</aside>'

search = []
for i,p in enumerate(pages):
    body = f'<button class="mobile-doc-button" aria-expanded="false" aria-controls="doc-sidebar">☰ 文档目录 · {esc(p["title"])}</button><div class="doc-shell">{sidebar(p["id"])}<main class="article" id="main"><div class="breadcrumbs"><a href="index.html">首页</a><span>/</span><a href="overview.html">文档</a><span>/</span>{esc(p["group"])}</div><h1>{esc(p["title"])}</h1><p class="lead">{esc(p["description"])}</p><div class="doc-meta">{BRAND} &nbsp; / &nbsp; 资料整理 {DATE}</div>'
    search.append(dict(title=p['title'],url=p['id']+'.html',text=p['description']))
    for s in p['sections']:
        content = s['html']
        content = re.sub(r'<div class="table-wrap">\s*(<table\b.*?</table>)\s*</div>',r'\1',content,flags=re.S)
        content = re.sub(r'(<table\b[^>]*>.*?</table>)',r'<div class="table-wrap">\1</div>',content,flags=re.S)
        body+=f'<section id="{esc(s["id"])}"><h2>{esc(s["title"])}</h2>{content}</section>'
        search.append(dict(title=p['title']+' · '+s['title'],url=p['id']+'.html#'+s['id'],text=plain(content)))
    if p.get('sources'): body+='<details class="sources"><summary>本页资料依据</summary><ul>'+''.join(f'<li>{esc(str(x))}</li>' for x in p['sources'])+'</ul></details>'
    body+='<nav class="page-nav" aria-label="上一篇与下一篇">'
    if i>0: body+=f'<a href="{pages[i-1]["id"]}.html"><small>← 上一篇</small>{pages[i-1]["title"]}</a>'
    if i+1<len(pages): body+=f'<a href="{pages[i+1]["id"]}.html"><small>下一篇 →</small>{pages[i+1]["title"]}</a>'
    body+='</nav></main><aside class="toc" aria-label="本页目录"><p>本页内容</p>'+''.join(f'<a href="#{s["id"]}">{esc(s["title"])}</a>' for s in p['sections'])+'</aside></div>'
    (OUT/(p['id']+'.html')).write_text(base(p['title'],p['description'],body,p['id']),encoding='utf-8')

home = '''<main id="main"><section class="hero"><div class="wrap"><div class="hero-inner"><div><div class="eyebrow">HARDWARE · SIGNAL · DEVELOPMENT</div><h1>智链脑机工坊<br><span>脑机接口开发文档</span></h1><p>连接硬件、数据与灵感。<br>从 BCI 采集板到多平台上位机，在这里找到产品资料、开发指南与代码接口。</p><div class="actions"><a class="btn primary" href="quickstart.html">快速开始 <span>→</span></a><a class="btn" href="overview.html">浏览产品文档</a></div><div class="signal-path"><span><b>01</b> 了解硬件</span><span>→</span><span><b>02</b> 连接设备</span><span>→</span><span><b>03</b> 采集与开发</span></div></div><div><div class="hero-art"><img src="assets/logo.webp" alt="智链脑机工坊品牌标志" width="264" height="264"></div><div class="hero-caption">可穿戴硬件 · 教学与研发</div></div></div></div></section>
<section class="section"><div class="wrap"><div class="section-title"><div><h2>选择你的开发板</h2><p>按产品系列查阅资料，按硬件版本开始开发。</p></div><a href="overview.html">全部文档 →</a></div><div class="product-grid"><a class="product-card" href="bci4.html"><img class="product-photo" src="assets/board4.webp" alt="4 通道 BCI V1.1 板卡 3D 图" width="450" height="340"><div class="product-info"><span class="tag">BCI V1.1 · 硬件资料</span><h3>4 通道 BCI</h3><p>板卡外观、接口识别、参考资料与开发准备。</p><span class="card-link">查看产品文档 →</span></div></a><a class="product-card" href="bci8.html"><img class="product-photo" src="assets/board8.webp" alt="8 通道 ESP32-S3 ADS1299 板卡实物" width="450" height="340"><div class="product-info"><span class="tag">ESP32-S3 + ADS1299</span><h3>8 通道 BCI</h3><p>采集固件、BLE 数据链路与多平台上位机开发。</p><span class="card-link">查看产品文档 →</span></div></a></div></div></section>
<section class="section" style="padding-top:12px"><div class="wrap"><div class="section-title"><div><h2>从这里继续</h2><p>让每一步开发，都有清晰的参考。</p></div></div><div class="entry-grid"><a class="entry" href="protocol.html"><span class="entry-num">01 / PROTOCOL</span><h3>固件与数据协议 ↗</h3><p>服务 UUID、控制命令、数据帧与数值换算。</p></a><a class="entry" href="python.html"><span class="entry-num">02 / DESKTOP</span><h3>Python 上位机 ↗</h3><p>查看实时波形，配置数字滤波，记录 CSV 数据。</p></a><a class="entry" href="android.html"><span class="entry-num">03 / MOBILE</span><h3>移动端开发 ↗</h3><p>Android 安装与连接，iOS 工程构建与使用说明。</p></a><a class="entry" href="downloads.html"><span class="entry-num">04 / RESOURCES</span><h3>资料与软件下载 ↗</h3><p>源码、安装包、PDF 文档和操作演示。</p></a></div></div></section><div class="wrap"><div class="notice-strip"><div><strong>第一次使用？先完成台架调试。</strong><p>核对板卡、供电和软件版本，用信号源或模拟器验证采集流程。</p></div><a class="btn" href="quickstart.html">阅读快速开始 →</a></div></div></main>'''
(OUT/'index.html').write_text(base('首页','智链脑机工坊 BCI 技术文档：4 通道与 8 通道采集板、ESP32-S3 固件、BLE 协议及 Python、Android、iOS 上位机。',home,'index'),encoding='utf-8')
(OUT/'assets'/'search-index.js').write_text('window.DOCS_SEARCH = '+json.dumps(search,ensure_ascii=False).replace('</','<\\/')+';\n',encoding='utf-8')
(OUT/'downloads'/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'.nojekyll').touch()
(OUT/'404.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>页面未找到 · 智链脑机工坊</title><body style="font-family:system-ui;margin:12vh auto;max-width:600px;padding:24px;color:#234"><h1>页面未找到</h1><p>地址可能已经变更。请返回文档首页，或使用站内搜索查找。</p><p><a href="index.html" id="back">返回首页 →</a></p><script>
(async () => {
  const parts = location.pathname.split('/').filter(Boolean);
  if (!location.pathname.endsWith('/')) parts.pop();
  while (true) {
    const candidate = '/' + (parts.length ? parts.join('/') + '/' : '') + 'index.html';
    try {
      const response = await fetch(candidate);
      if (response.ok && (await response.text()).includes('BCI DEVELOPMENT DOCS')) {
        document.getElementById('back').href = candidate;
        break;
      }
    } catch {}
    if (!parts.length) break;
    parts.pop();
  }
})();
</script></body></html>''',encoding='utf-8')
print(f'Built {len(pages)+2} HTML pages, {len(search)} search entries, {len(manifest)} downloads.')
