#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


def _read_env_flag(env_path: Path, key: str) -> bool:
    if not env_path.exists():
        return False
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return False


def _remove_nlg_block(lines: list[str]) -> list[str]:
    new_lines: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("nlg:"):
            idx += 1
            while idx < len(lines) and (lines[idx].startswith("  ") or not lines[idx].strip()):
                idx += 1
            continue
        new_lines.append(line)
        idx += 1
    return new_lines


def _insert_nlg_block(lines: list[str], model_group: str) -> list[str]:
    nlg_block = [
        "nlg:",
        "  type: rephrase",
        "  llm:",
        f"    model_group: {model_group}",
        "",
    ]
    new_lines: list[str] = []
    inserted = False
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        new_lines.append(line)
        if not inserted and line.startswith("action_endpoint:"):
            idx += 1
            while idx < len(lines):
                new_lines.append(lines[idx])
                if not lines[idx].startswith("  "):
                    break
                idx += 1
            new_lines.extend(nlg_block)
            inserted = True
            continue
        idx += 1
    if not inserted:
        new_lines = nlg_block + new_lines
    return new_lines


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    endpoints_path = root / "endpoints.yml"
    enabled = _read_env_flag(env_path, "ENABLE_REPHRASE")
    model_group = "gemini_command_generation_model"

    lines = endpoints_path.read_text().splitlines()
    lines = _remove_nlg_block(lines)
    if enabled:
        lines = _insert_nlg_block(lines, model_group)
    endpoints_path.write_text("\n".join(lines) + "\n")

    state = "enabled" if enabled else "disabled"
    print(f"Rephrase NLG {state}.")


if __name__ == "__main__":
    main()
