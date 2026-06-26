"""人员编号、资料合并（跨频道同一人）。"""

from __future__ import annotations

import hashlib
import re

from template_extract import extract_profile_fields

MERGE_FIELD_KEYS = (
    "review_count",
    "overall_score",
    "good_rate",
    "mid_rate",
    "bad_rate",
    "photo_score",
    "service_score",
    "face_score",
    "attitude_score",
    "body_score",
    "env_score",
    "name",
    "age",
    "height",
    "weight",
    "cup",
    "project",
    "price_once",
    "price_twice",
    "region",
    "telegram",
    "channel",
    "duplex",
)

REGION_ALIASES = {
    "清江浦": "清江浦区",
    "淮阴": "淮阴区",
    "淮安": "淮安区",
    "洪泽": "洪泽区",
    "涟水": "涟水县",
    "盱眙": "盱眙县",
    "金湖": "金湖县",
}


def normalize_region(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"\s+", "", s)
    if not s:
        return ""
    if s in REGION_ALIASES:
        return REGION_ALIASES[s]
    if not s.endswith(("区", "县", "市")) and s in REGION_ALIASES:
        return REGION_ALIASES[s]
    for key, val in REGION_ALIASES.items():
        if s == key or s.startswith(key):
            return val
    return s


def normalize_person_name(raw: str) -> str:
    if not raw:
        return ""
    cleaned = re.sub(r"[（(].*?[)）]", "", raw).strip()
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned or raw.strip()


def make_person_id(name: str, region: str) -> str:
    n = normalize_person_name(name).lower()
    r = normalize_region(region).lower()
    if not n or not r:
        return ""
    key = f"{n}|{r}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def fields_from_text(text: str) -> dict[str, str]:
    fields = extract_profile_fields(text or "")
    if fields.get("name"):
        fields["name"] = normalize_person_name(fields["name"])
    if fields.get("region"):
        fields["region"] = normalize_region(fields["region"])
    return fields


def person_id_from_text(text: str) -> str:
    fields = fields_from_text(text)
    return make_person_id(fields.get("name", ""), fields.get("region", ""))


def merge_profile_fields(base: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    out = {k: (base.get(k) or "") for k in MERGE_FIELD_KEYS}
    for key in MERGE_FIELD_KEYS:
        val = (new.get(key) or "").strip()
        if not val:
            continue
        cur = (out.get(key) or "").strip()
        if not cur:
            out[key] = val
        elif cur == val:
            continue
        elif key in ("name", "region"):
            out[key] = val
        elif len(val) > len(cur):
            out[key] = val
    if out.get("name"):
        out["name"] = normalize_person_name(out["name"])
    if out.get("region"):
        out["region"] = normalize_region(out["region"])
    return out


def count_filled_fields(fields: dict[str, str]) -> int:
    return sum(1 for k in MERGE_FIELD_KEYS if (fields.get(k) or "").strip())


def render_fields_template(template: str, fields: dict[str, str]) -> str:
    safe = {k: (fields.get(k) or "") for k in MERGE_FIELD_KEYS}
    safe["raw"] = ""
    try:
        return template.format_map(safe).strip()
    except Exception:
        return ""
