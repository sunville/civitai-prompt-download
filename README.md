# Civitai 提示词批量下载器 · 项目工作区

> 当前阶段：**需求对齐中**（按约定，未开始写代码）。最终交付：Windows 可双击运行的 `.exe`（可视化界面），批量下载 `civitai.com` / `civitai.red` 的 AI 绘画提示词与生成参数。

## 目录

| 路径 | 说明 | 给谁看 |
| --- | --- | --- |
| `docs/00-需求调研问卷.md` | 需求调研问卷（可编辑版，按题号回填） | 客户填写 |
| `docs/00-需求调研问卷.docx` | 同上，Word 版（可直接在 Word 里勾选/填空） | 客户填写 |
| `docs/02-需求理解确认书.md` / `.docx` | 依据回填问卷整理的需求理解 + 8 个待确认点 + MVP 规格 + 技术方案 + 风险 | 双方确认 |
| `docs/01-技术预研摘要.md` | 需求阶段技术预研：两站关系、官方 API 能力、限流、坑清单、MVP 路线（含来源链接，区分【文档】/【实测】/【第三方】/【推测】） | 内部参考 |
| `tools/md_to_docx.py` | 问卷 Markdown → Word 的转换脚本（表格/列表/代码块/加粗/复选框） | 维护用 |

## 工作流

1. 客户填写问卷（或直接回复“按推荐方案”，8 个关键题见问卷附A）
2. PM 出《需求规格说明书 V1.0 + 界面原型草图》
3. 客户确认后进入编码
4. 交付 `exe` + 《使用说明》，按问卷第 9 节验收

## 重新生成 Word 问卷

```powershell
$py = "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
& $py tools\md_to_docx.py "docs\00-需求调研问卷.md" "docs\00-需求调研问卷.docx"
```

## 已确认的关键技术事实（摘要）

- `civitai.com` 与 `civitai.red` 是**同一套后端的两套内容视图**（`.com`=SFW，`.red`=全部），官方 REST API 完全相同，**无需两套逻辑**。
- 采集走官方 `GET /api/v1/images?withMeta=true`：匿名即可拿到 `prompt` / `negativePrompt` / 采样器 / 步数 / CFG / seed / 尺寸 / LoRA 列表 / 分级 / 原图 URL，**含 NSFW**。
- 网页 HTML 解析不可靠（Cloudflare 403 + tRPC 前端指纹），**不进 MVP**。
- 翻页硬约束：`page × limit ≤ 1000`，全量必须用 `metadata.nextCursor`。
- 元数据质量参差（meta 可为空、负向提示词常缺、各工具字段名不同）→ 数据完整度不能承诺 100%。

详见 `docs/01-技术预研摘要.md`。
