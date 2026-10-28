"""Offline codec check: the quest query response order matches the 4.3.4 pin.

This is a regression guard on the field order and loop repetition, not a proof
of the wire bytes: widths and runtime values need the C++ packet test.

python3 apps/cata/test_quest_query_layout.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "questquery-434-layout.json"
SOURCE = ROOT / "src/server/game/Quests/QuestDef.cpp"
HEADER = ROOT / "src/server/game/Quests/QuestDef.h"


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def query_statements() -> list[str]:
    body = strip_comments(SOURCE.read_text()).split("void Quest::InitializeQueryData", 1)[1]
    return re.findall(r"queryData\s*<<\s*(.*?);", body.split("\n}", 1)[0], re.S)


def operands(statement: str) -> list[str]:
    return [re.sub(r"\s+", " ", part).strip() for part in statement.split("<<") if part.strip()]


def loop_bounds() -> list[int]:
    body = strip_comments(SOURCE.read_text()).split("void Quest::InitializeQueryData", 1)[1]
    bounds = {m[0]: int(m[1]) for m in re.findall(r"#define\s+(QUEST_\w+)\s+(\d+)", HEADER.read_text())}
    return [bounds[m] for m in re.findall(r"for\s*\(.*?i\s*<\s*(\w+);", body.split("\n}", 1)[0], re.S)]


def check_query_layout() -> None:
    expected = json.loads(FIXTURE.read_text())
    actual_operands = [field for statement in query_statements() for field in operands(statement)]
    assert actual_operands == expected["fields"], "quest query operand order drifted from the 4.3.4 pin"
    assert loop_bounds() == expected["loop_bounds"], "quest query loop repetition drifted from the 4.3.4 pin"


if __name__ == "__main__":
    check_query_layout()
    print(f"quest query layout matches the pin ({len(query_statements())} statements)")
