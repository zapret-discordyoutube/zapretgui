from __future__ import annotations

from pathlib import Path

from settings import store as settings_store

def resume_state_path() -> Path:
    return settings_store.get_settings_database_path()


def scan_key(
    target: str,
    scan_protocol: str = "tcp_https",
    udp_games_scope: str = "all",
) -> str:
    normalized_target = (target or "").strip().lower()
    normalized_protocol = (scan_protocol or "tcp_https").strip().lower() or "tcp_https"
    if not normalized_target:
        return ""

    if normalized_protocol == "udp_games":
        scope = (udp_games_scope or "all").strip().lower()
        if scope not in {"all", "games_only"}:
            scope = "all"
        return f"{normalized_protocol}|{scope}|{normalized_target}"

    return f"{normalized_protocol}|{normalized_target}"


def load_resume_state() -> dict:
    empty_state = {"domains": {}}
    try:
        data = settings_store.get_blockcheck_settings().get("scan_resume", {})
        if not isinstance(data, dict):
            return empty_state

        domains = data.get("domains")
        if isinstance(domains, dict):
            cleaned_domains = {}
            for raw_key, raw_value in domains.items():
                raw_key_str = str(raw_key).strip().lower()
                if not raw_key_str:
                    continue
                if "|" in raw_key_str:
                    parts = raw_key_str.split("|")
                    if len(parts) == 2 and parts[0] == "udp_games":
                        key = f"udp_games|all|{parts[1]}"
                    else:
                        key = raw_key_str
                else:
                        key = scan_key(raw_key_str, "tcp_https")
                if not key:
                    continue
                if isinstance(raw_value, dict):
                    raw_index = raw_value.get("next_index", 0)
                else:
                    raw_index = raw_value
                try:
                    next_index = max(0, int(raw_index))
                except Exception:
                    next_index = 0
                cleaned_domains[key] = {"next_index": next_index}
            return {"domains": cleaned_domains}

        key = scan_key(str(data.get("target", "") or ""))
        try:
            next_index = max(0, int(data.get("next_index", 0) or 0))
        except Exception:
            next_index = 0
        if key and next_index > 0:
            return {"domains": {key: {"next_index": next_index}}}
        return empty_state
    except Exception:
        return empty_state


def write_resume_state(state: dict) -> None:
    try:
        settings_store.set_blockcheck_settings({"scan_resume": state})
    except Exception:
        pass


def get_resume_index(target: str, scan_protocol: str, udp_games_scope: str = "all") -> int:
    key = scan_key(target, scan_protocol, udp_games_scope)
    if not key:
        return 0
    state = load_resume_state()
    domains = state.get("domains", {})
    entry = domains.get(key, {})
    try:
        return max(0, int(entry.get("next_index", 0) or 0))
    except Exception:
        return 0


def save_resume_state(
    target: str,
    scan_protocol: str,
    next_index: int,
    udp_games_scope: str = "all",
) -> None:
    key = scan_key(target, scan_protocol, udp_games_scope)
    if not key:
        return
    state = load_resume_state()
    domains = state.setdefault("domains", {})
    domains[key] = {"next_index": max(0, int(next_index))}
    write_resume_state(state)


def clear_resume_state(target: str, scan_protocol: str, udp_games_scope: str = "all") -> None:
    key = scan_key(target, scan_protocol, udp_games_scope)
    if not key:
        return
    state = load_resume_state()
    domains = state.get("domains", {})
    if key in domains:
        del domains[key]

    if domains:
        state["domains"] = domains
        write_resume_state(state)
    else:
        write_resume_state({"domains": {}})
