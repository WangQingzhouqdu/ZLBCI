# 智链脑机工坊 · 技术文档网站

已生成可直接发布的静态网站。首页在 `docs/index.html`，不需要安装 Node.js，也不需要运行构建才能上线。

## 上传到 GitHub Pages

1. 解压网站压缩包，创建一个 GitHub 仓库，例如 `bci-docs`。使用 GitHub Free 时请选择公开仓库。
2. 上传当前目录里的文件和文件夹，保持 `docs/index.html` 这一层级。不要只上传 ZIP；不要额外套一层 `bci-docs-site` 文件夹。
3. 打开仓库 **Settings → Pages**。
4. 在 **Build and deployment → Source** 选择 **Deploy from a branch**。
5. 选择上传所用的分支（通常为 `main`），目录选择 **`/docs`**，点击 **Save**。
6. 等待 Pages 部署完成，访问设置页显示的网址。普通项目仓库通常为 `https://你的用户名.github.io/bci-docs/`。

所有网页、图片和下载链接均使用相对路径，支持项目仓库子路径。`docs/.nojekyll` 已提供；通过 Git 或 GitHub Desktop 上传可保留该隐藏文件。网站没有以 `_` 开头的资源，浏览器漏传这个空文件时也不依赖其运行。

本包少于 100 个文件，且每个文件都小于 GitHub 网页单文件 25 MiB 限制。也可使用 GitHub Desktop 把全部内容提交并推送。

GitHub 官方依据：[发布来源设置](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)、[创建 Pages 网站](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)、[上传文件限制](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository)。

## 本地查看

双击 `docs/index.html` 即可浏览正文、搜索、图片与下载链接。代码复制在本地文件模式可能受浏览器限制。

也可以在此目录打开终端，运行：

```powershell
python -m http.server 8000 --bind 127.0.0.1
```

浏览器访问 `http://127.0.0.1:8000/docs/`。关闭预览时，在终端按 `Ctrl+C`。

## 内容与维护

- `docs/`：直接发布的完整站点，包含 15 个 HTML 页面、图片、搜索索引与 10 项下载。
- `content/`：已整理的结构化文档内容，UTF-8 JSON。
- `scripts/build.py`：重新生成 HTML 和全文搜索索引，仅需 Python 标准库。
- `scripts/prepare_assets.py`：从原始资料目录重新制作明确选中的网页素材；仅刷新原始素材时需要 Pillow 与本机 `D:/淘宝店铺/`。
- `scripts/validate.py`：检查本地链接、锚点、资源、文件大小与下载校验。

修改文档：编辑 `content/technical.json`、`content/mobile.json` 或 `content/four-channel.json` 的标题和章节内容，然后运行：

```powershell
python scripts/build.py
python scripts/validate.py
```

首页、通用导航、下载中心与资料来源页面在 `scripts/build.py` 中维护；样式在 `docs/assets/style.css`，交互在 `docs/assets/app.js`。完成后，将更新过的 `content/`、脚本和 `docs/` 一起提交至 GitHub。

新增下载：先将文件放入 `docs/downloads/`，再在 `content/assets.json` 中增加 `file`、`title`、`kind`、`note`、`source` 条目。运行构建后，文件大小、SHA-256 与下载页面会更新。Android/iOS 源码 ZIP 已由构建脚本单独纳入，不用重复登记。

## 资料范围与版本

此网站根据提供的产品资料和代码整理，未执行板卡、BLE 真机或移动端应用测试。各页面保留资料依据，并区分当前源码、演示素材与尚需确认的硬件指标。

- 4 通道资料目录中的 248.97 MB 参考包包含另一套 STM32 八通道工程；本站解释其用途，没有把参考参数套用于交付板卡，也没有收入大压缩包。
- 8 通道当前固件采用 ESP32-S3 / ADS1299 / BLE。采样率档位与代码默认配置按源码整理，不代表稳定吞吐或实测精度。
- Android 包为原资料中的 `EEG-Mobile-v1.0.0.apk`。
- iOS 为 React Native / Expo 源码，需要在 Mac 上构建和配置签名，没有已签名 IPA。
- 公开源码包按允许列表制作，移除了本机配置与签名信息；Android 的副本改为自行配置发行签名。
- 原始 PDF 为 2026-10-07 版本，部分“待确认”条目已由网站的当前源码说明补充。

内部定价、成本表、经营分析、应用签名密钥、密码配置、构建缓存、历史备份与采集记录未纳入。原始资料保持不变。未擅自添加开源许可证，相关源码及第三方依赖的使用和再分发按原有授权处理。

品牌使用提供的 `logo.png`；网站的信息架构参考 [nologo.tech](https://www.nologo.tech/)，未复用对方的品牌、联系方式或产品内容。
