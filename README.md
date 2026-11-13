# Civitai 提示词批量下载器

批量下载 civitai（`civitai.com` / `civitai.red`）上公开图片的**提示词与生成参数**的 Windows 桌面工具。
单文件绿色 exe，中文界面，结果存本地 SQLite，支持断点续传、去重、CSV / JSONL / TXT / Excel 导出。

> 当前版本：**v0.1.3（MVP）**，交付物：`dist/CivitaiPromptDL.exe` + 使用说明 + 误报处理说明。
>
> v0.1.3：正向提示词自动删除尖括号内容（<lora:...> 等）；导出字段默认只勾选正向提示词，新增「全部取消」；默认文件名改为「checkpoint_排序_周期/日期范围」。
>
> v0.1.2：数量上限输入框支持直接键盘输入（回车确认/失焦自动规范化）。
>
> v0.1.1 修复：部分模型官方 `modelId` 过滤返回 0 条的问题（改为按模型版本逐个抓取）；「没有抓到数据」的已完成任务可直接选中后点「开始 / 继续」重跑。

---

## 快速开始

1. 双击 `dist/CivitaiPromptDL.exe`（Win10/11 64 位，免安装）；
2. 打开「设置」填 API Key（非必需，但推荐；设置里有获取指引）；
3. 左侧选采集方式（按模型 / LoRA / 作者 / 榜单 / 关键词 / 链接列表）与过滤条件 → 点「加入队列并开始」；
4. 任务完成后在下方预览，点「导出」保存文件。

详细操作见 [使用说明](docs/03-使用说明.md)；杀软误报见 [误报处理说明](docs/04-误报处理说明.md)。

---

## 功能范围（MVP）

- **采集入口**：按模型/Checkpoint、按 LoRA、按作者、按榜单、关键词（先搜模型）、图片/页面链接列表、站内搜索（实验）
- **过滤**：内容级别（全部/SFW/NSFW）、排序、周期、日期范围、数量上限、生成工具、提示词 Tags、多条件叠加
- **字段**：正向/负向提示词、LoRA（名称+权重）、Checkpoint、生成工具、Tags、图片 ID/页面链接、模型 ID/版本 ID、基础模型、NSFW 分级、原始 JSON
- **存储**：本地 SQLite；图片按 `imageId` 全库去重；同任务内提示词文本去重；断点续传
- **导出**：CSV（UTF-8 BOM，Excel 直开不乱码）、JSON Lines、TXT（仅正向提示词、无表头、每行一条）、XLSX；可选字段、可按 NSFW 分级拆分
- **界面**：任务台布局、实时进度/日志、结果预览与列设置、设置页、历史任务、系统托盘
- **交付**：PyInstaller 单文件 exe（约 26 MB，无控制台、无 UPX），附使用说明与误报处理说明

### 已知限制

- 官方 API **不支持按关键词搜索图片**：关键词入口会先搜模型，再抓该模型下的图；
- 站内分类标签（taxonomy tag）不在图片接口里返回，第一版不提供站内标签筛选；「Tags」列为从 prompt 拆出的 tag；
- 日期范围仅对「最新 / 最旧」排序精确生效，其他排序只能用周期近似；
- 部分图片作者未公开提示词（meta 为空）会被跳过，数据完整度不承诺 100%；
- 单张成熟内容图片的匿名查询受限，建议填 API Key；
- 「站内搜索图片（实验）」依赖非官方接口，可能随时失效。

---

## 项目结构

```
civitai_dl/            程序包
  api.py               civitai REST 客户端（限速、重试、代理、API Key）
  parser.py            meta 解析（提示词 / LoRA / Checkpoint / 工具 / tags / URL）
  store.py             SQLite 存储（任务、结果、模型名缓存）
  runner.py            任务执行器（分页、过滤、断点续传、暂停/取消）
  resolver.py          模型/版本名称解析
  search.py            实验性站内图片搜索
  export.py            CSV / JSONL / TXT / XLSX 导出
  config.py            设置与常量
  paths.py  errors.py  路径与异常
  selftest.py          自检（打包后可用 --selftest 验证）
  ui/                  Tkinter 界面（app.py / dialogs.py / widgets.py / theme.py）
  main.py              入口
assets/                图标
docs/                  需求/预研/确认书/使用说明/误报说明
scripts/               构建脚本、版本信息
tests/                 单元测试（unittest）
civitai_dl.spec        PyInstaller 配置
run_app.py             打包入口
```

---

## 开发

### 环境

- Windows 10/11，Python 3.12
- 依赖：`pyinstaller`、`pystray`、`Pillow`（openpyxl 已随环境提供；代码只用标准库 + 这些库）

```powershell
$py = "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
& $py -m pip install pyinstaller pystray --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### 运行与自检

```powershell
# 打开界面
& $py -m civitai_dl

# 自检（不打开界面）
& $py -m civitai_dl --selftest

# 自检 + 真实 API 请求
& $py -m civitai_dl --selftest --live

# 单元测试
& $py -m unittest discover -s tests -v
```

### 打包

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物：`dist/CivitaiPromptDL.exe`，同时把使用说明/误报说明复制到 `dist/`。

> 发布策略：`dist/` **只保留当前版本的 exe**，不保留旧版本备份（历史版本由 git tag 管理）。
> 打包时如果程序正在运行，脚本会提示先关闭程序再构建。

---

## 数据位置

| 内容 | 路径 |
| --- | --- |
| 配置 | `%APPDATA%\CivitaiPromptDL\config.json` |
| 数据库 | `%APPDATA%\CivitaiPromptDL\data.db` |
| 日志 | `%APPDATA%\CivitaiPromptDL\logs\app.log` |
| 默认导出 | exe 同目录 `output\`（可在设置里改） |

程序不写注册表、不自启；卸载 = 删除 exe + 上述目录。

---

## 文档

- [需求调研问卷](docs/00-需求调研问卷.md) / [技术预研摘要](docs/01-技术预研摘要.md) / [需求理解确认书](docs/02-需求理解确认书.md)
- [使用说明](docs/03-使用说明.md) / [误报处理说明](docs/04-误报处理说明.md)

---

## 合规

本工具只用于下载公开可见的提示词与元数据，请遵守 civitai 服务条款与速率限制；不采集私密内容、不处理未成年人性相关内容；提示词与图片版权归原作者所有。
