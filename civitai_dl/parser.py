"""把 API 返回的图片对象解析成结构化记录：提示词、LoRA、Checkpoint、工具、tags。"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

# ---------------------------------------------------------------- 工具识别

_TOOL_ONSITE_KEYS = {"ecosystem", "engine", "priority", "quantity", "resolution", "Edit mode", "Edit images count"}
_TOOL_COMFY_KEYS = {"comfy", "workflow"}
_TOOL_WEBUI_KEYS = {"Model", "sampler", "steps", "resources", "hashes", "RNG", "Schedule type", "Model hash"}


def normalize_meta(meta: Any) -> dict[str, Any]:
    """统一 meta 形状：部分接口返回 {"id":..., "meta": {...}} 的双层嵌套。"""
    if not isinstance(meta, dict):
        return {}
    inner = meta.get("meta")
    if isinstance(inner, dict):
        if any(key in inner for key in ("prompt", "negativePrompt", "Model", "comfy", "workflow", "sampler", "steps", "resources", "civitaiResources")):
            return inner
        if set(meta.keys()) <= {"id", "meta", "url", "hash"}:
            return inner
    return meta


def classify_tool(meta: dict[str, Any] | None) -> str:
    """按 meta 的特征键识别图片的生成工具。"""
    meta = normalize_meta(meta)
    if not meta:
        return "无参数"
    keys = set(meta.keys())
    if "App" in keys:
        return "SD.Next"
    if keys & _TOOL_ONSITE_KEYS:
        return "站内生成器"
    if keys & _TOOL_COMFY_KEYS:
        return "ComfyUI"
    if keys & _TOOL_WEBUI_KEYS:
        return "WebUI"
    if "prompt" in keys:
        return "其他"
    return "其他"


# ---------------------------------------------------------------- LoRA 解析

_LORA_PROMPT_RE = re.compile(r"<(?:lora|lyco):([^:>]+)(?::([^:>]+))?>", re.IGNORECASE)


def _weight(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _fmt_weight(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return f"{int(round(value))}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _clean_lora_name(name: str) -> str:
    name = str(name).strip().strip('"')
    for suffix in (".safetensors", ".ckpt", ".pt", ".bin"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return name


def extract_loras(meta: dict[str, Any] | None, prompt: str, resolved: Callable[[int], dict[str, Any] | None] | None = None) -> str:
    """合并 prompt 语法、resources、hashes、civitaiResources 中的 LoRA，输出 "name:weight; ..." """
    order: list[str] = []
    weights: dict[str, float] = {}

    def add(name: str, weight: float) -> None:
        name = _clean_lora_name(name)
        if not name:
            return
        key = name.lower()
        if key not in weights:
            order.append(name)
            weights[key] = weight

    for match in _LORA_PROMPT_RE.finditer(prompt or ""):
        add(match.group(1), _weight(match.group(2)) if match.group(2) else 1.0)

    meta = meta or {}
    for res in meta.get("resources") or []:
        if not isinstance(res, dict):
            continue
        if str(res.get("type", "")).lower() != "lora":
            continue
        name = res.get("name") or res.get("hash") or ""
        add(name, _weight(res.get("weight", 1.0)))

    for key in (meta.get("hashes") or {}):
        if isinstance(key, str) and key.lower().startswith("lora:"):
            add(key.split(":", 1)[1], 1.0)

    for res in meta.get("civitaiResources") or []:
        if not isinstance(res, dict):
            continue
        if str(res.get("type", "")).lower() != "lora":
            continue
        version_id = res.get("modelVersionId") or res.get("modelVersionID")
        name = ""
        if version_id and resolved:
            info = resolved(int(version_id))
            if info and info.get("name"):
                name = info["name"]
        if not name and version_id:
            name = f"版本ID {version_id}"
        add(name, _weight(res.get("weight", 1.0)))

    return "; ".join(f"{name}:{_fmt_weight(weights[name.lower()])}" for name in order)


# ---------------------------------------------------------------- Checkpoint

def extract_checkpoint(
    item: dict[str, Any],
    meta: dict[str, Any] | None,
    resolved: Callable[[int], dict[str, Any] | None] | None = None,
) -> tuple[str, int | None, int | None]:
    """返回 (checkpoint 名称, 主模型 ID, 主模型版本 ID)。"""
    meta = meta or {}
    model_id, version_id = None, None
    name = ""

    for key in ("Model", "Model name", "model"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()
            break

    # 站内生成器常把 checkpoint 写成 AIR URN：urn:air:sdxl:checkpoint:civitai:1224788@2864868
    if name:
        urn = re.search(r"urn:air:[^:]*:checkpoint:civitai:(\d+)@(\d+)", name, re.IGNORECASE)
        if urn:
            model_id = int(urn.group(1))
            version_id = int(urn.group(2))
            if resolved:
                info = resolved(version_id)
                if info and info.get("name"):
                    name = str(info["name"])
            if name.lower().startswith("urn:air:"):
                name = str(item.get("baseModel") or f"模型 {model_id}")

    if not name:
        for res in meta.get("resources") or []:
            if isinstance(res, dict) and str(res.get("type", "")).lower() in ("checkpoint", "model"):
                if res.get("name"):
                    name = str(res["name"])
                    break

    checkpoint_version = version_id
    if checkpoint_version is None:
        for res in meta.get("civitaiResources") or []:
            if isinstance(res, dict) and str(res.get("type", "")).lower() in ("checkpoint", "model"):
                checkpoint_version = res.get("modelVersionId") or res.get("modelVersionID")
                break

    if checkpoint_version and resolved:
        info = resolved(int(checkpoint_version))
        if info:
            if not name:
                name = str(info.get("name") or "")
            if info.get("model_id"):
                model_id = info["model_id"]
            version_id = version_id or int(checkpoint_version)

    if not name:
        ids = item.get("modelVersionIds") or []
        if ids and resolved:
            info = resolved(int(ids[0]))
            if info:
                name = str(info.get("name") or "")
                model_id = info.get("model_id")
                version_id = int(ids[0])
        if not name:
            name = str(item.get("baseModel") or "")

    if version_id is None:
        ids = item.get("modelVersionIds") or []
        if ids:
            version_id = int(ids[0])
    return name, model_id, version_id


# ---------------------------------------------------------------- tags

_LORA_TOKEN_RE = re.compile(r"<[^>]+>")
_TRAILING_WEIGHT_RE = re.compile(r":\s*\d+(?:\.\d+)?\s*$")


def _clean_tag(token: str) -> str:
    text = token.strip()
    text = _LORA_TOKEN_RE.sub("", text)
    text = text.replace("\\(", "(").replace("\\)", ")").replace("\\[", "[").replace("\\]", "]")
    # 反复剥掉包裹的权重括号 / 方括号 / 花括号
    for _ in range(3):
        stripped = text.strip()
        if len(stripped) >= 2 and stripped[0] in "([{" and stripped[-1] in ")]}":
            text = stripped[1:-1]
            continue
        break
    text = _TRAILING_WEIGHT_RE.sub("", text.strip())
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return text


def prompt_tags(prompt: str) -> list[str]:
    """从提示词拆 tag（去掉 LoRA 语法与权重括号），按出现顺序去重。"""
    seen: set[str] = set()
    tags: list[str] = []
    for raw in (prompt or "").split(","):
        tag = _clean_tag(raw)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def tags_text(prompt: str) -> str:
    return ", ".join(prompt_tags(prompt))


def prompt_hash(prompt: str) -> str:
    normalized = re.sub(r"\s+", " ", (prompt or "").strip()).lower()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest() if normalized else ""


# ---------------------------------------------------------------- 整条记录

def build_record(
    item: dict[str, Any],
    site: str,
    resolved: Callable[[int], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """把 API image 对象转成写入 SQLite 的记录。"""
    meta = normalize_meta(item.get("meta"))
    prompt = str(meta.get("prompt") or "")
    negative = str(meta.get("negativePrompt") or meta.get("negative_prompt") or "")
    checkpoint, model_id, version_id = extract_checkpoint(item, meta, resolved)
    lora = extract_loras(meta, prompt, resolved)
    image_id = int(item.get("id"))
    return {
        "image_id": image_id,
        "page_url": f"https://{site}/images/{image_id}",
        "image_url": str(item.get("url") or ""),
        "prompt": prompt,
        "negative_prompt": negative,
        "tags": tags_text(prompt),
        "lora": lora,
        "checkpoint": checkpoint,
        "tool": classify_tool(meta),
        "base_model": str(item.get("baseModel") or ""),
        "model_id": model_id,
        "model_version_id": version_id,
        "nsfw_level": str(item.get("nsfwLevel") or ""),
        "username": str(item.get("username") or ""),
        "created_at": str(item.get("createdAt") or ""),
        "raw_json": json.dumps(item, ensure_ascii=False),
        "prompt_hash": prompt_hash(prompt),
        "image_type": str(item.get("type") or ""),
    }


# ---------------------------------------------------------------- URL 解析

_URL_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("image", re.compile(r"/images/(\d+)", re.I)),
    ("post", re.compile(r"/posts/(\d+)", re.I)),
    ("model", re.compile(r"/models/(\d+)", re.I)),
    ("model_version", re.compile(r"/model-versions/(\d+)", re.I)),
    ("user", re.compile(r"/(?:user|users)/([A-Za-z0-9_.\-]+)", re.I)),
    ("download", re.compile(r"/api/download/models/(\d+)", re.I)),
]


def parse_target_url(text: str) -> tuple[str, Any] | None:
    """解析用户粘贴的链接：('image'|'post'|'model'|'model_version'|'user'|'download', value)。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return ("image", int(text))
    if not re.match(r"https?://", text, re.I):
        text = "https://" + text
    for kind, pattern in _URL_RULES:
        match = pattern.search(text)
        if match:
            value = match.group(1)
            if kind == "user":
                return (kind, value)
            return (kind, int(value))
    return None


def url_query_value(kind: str, value: Any) -> tuple[str, Any] | None:
    """把解析结果转成 /images 的查询参数。"""
    mapping = {
        "image": ("imageId", value),
        "post": ("postId", value),
        "model": ("modelId", value),
        "model_version": ("modelVersionId", value),
        "download": ("modelVersionId", value),
        "user": ("username", value),
    }
    return mapping.get(kind)
