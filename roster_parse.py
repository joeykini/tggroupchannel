"""解析出勤 Bot 回复的在岗名单。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from person_registry import make_person_id, normalize_person_name, normalize_region


@dataclass
class RosterEntry:
    region: str
    name: str
    status: str  # online | resting
    person_id: str = ""

    def __post_init__(self) -> None:
        self.region = normalize_region(self.region)
        self.name = normalize_person_name(self.name)
        if not self.person_id:
            self.person_id = make_person_id(self.name, self.region)


_DISTRICT_RE = re.compile(r"【([^】]+)】")


def _parse_name_chunk(chunk: str, region: str) -> list[RosterEntry]:
    entries: list[RosterEntry] = []
    chunk = chunk.strip()
    if not chunk or not region:
        return entries

    parts = re.split(r"(?=🟢|🔴)", chunk)
    if len(parts) == 1 and "🟢" not in chunk and "🔴" not in chunk:
        for token in re.split(r"\s+", chunk):
            name = normalize_person_name(token)
            if name and len(name) >= 2:
                entries.append(RosterEntry(region=region, name=name, status="online"))
        return entries

    for part in parts:
        part = part.strip()
        if not part:
            continue
        status = "online"
        if part.startswith("🔴"):
            status = "resting"
            part = part[1:].strip()
        elif part.startswith("🟢"):
            part = part[1:].strip()
        name = normalize_person_name(part)
        if name:
            entries.append(RosterEntry(region=region, name=name, status=status))
    return entries


def parse_roster_text(text: str) -> list[RosterEntry]:
    if not text or "【" not in text:
        return []

    entries: list[RosterEntry] = []
    current_region = ""

    for line in (text or "").replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue

        pos = 0
        matches = list(_DISTRICT_RE.finditer(line))
        if not matches:
            if current_region:
                entries.extend(_parse_name_chunk(line, current_region))
            continue

        for i, m in enumerate(matches):
            current_region = normalize_region(m.group(1))
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            segment = line[start:end].strip()
            entries.extend(_parse_name_chunk(segment, current_region))

    dedup: dict[str, RosterEntry] = {}
    for e in entries:
        if e.person_id:
            dedup[e.person_id] = e
    return list(dedup.values())
