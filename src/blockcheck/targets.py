"""Target loading — встроенные списки, внешние переопределения, домены пользователя.

Базовые списки живут в :mod:`blockcheck.data_lists`, а не в data-файлах: в
собранном приложении их там не оказывалось, и диагностика молча работала на
урезанном наборе. Файл рядом с приложением по-прежнему может переопределить
список — но теперь это осознанный выбор пользователя, а не тихая деградация.
"""

import json
import logging
from pathlib import Path

from blockcheck.data_lists import (
    DNS_EXTRA_DOMAINS,
    HTTPS_TARGETS,
    PING_TARGETS,
    STUN_TARGETS,
    TCP_16_20_TARGETS,
)
from blockcheck.config import TCP_TARGET_MAX_COUNT, TCP_TARGETS_PER_PROVIDER
from blockcheck.googlevideo_discovery import (
    is_bare_googlevideo_host,
    normalize_googlevideo_host,
)
from blockcheck.hosts import host_of, is_pseudo_target
from config.runtime_layout import APPLICATION_PATHS
from settings import store as settings_store

logger = logging.getLogger(__name__)


def _iter_override_candidates(filename: str, filepath: str | Path | None = None) -> list[Path]:
    """Пути, по которым пользователь может положить свой список."""
    if filepath is not None:
        return [Path(filepath)]

    app_dir = APPLICATION_PATHS.root
    candidates = [
        app_dir / "blockcheck" / "data" / filename,
        app_dir / "data" / filename,
    ]

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


# ---------------------------------------------------------------------------
# Built-in domain list
# ---------------------------------------------------------------------------

def load_domains(filepath: str | Path | None = None) -> list[str]:
    """Load domain list from file (one domain per line, # comments)."""
    domains, _ = load_domains_with_source(filepath)
    return domains


def load_domains_with_source(filepath: str | Path | None = None) -> tuple[list[str], str]:
    """Домены для DNS-проверки и источник, из которого они взяты."""
    builtin = get_dns_check_domains()

    for candidate in _iter_override_candidates("domains.txt", filepath):
        if not candidate.exists():
            continue
        try:
            domains = [
                line.strip()
                for line in candidate.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        except OSError as e:
            logger.warning("Failed to load domains from %s: %s", candidate, e)
            return builtin, f"builtin (unreadable override {candidate})"

        if domains:
            return domains, f"file:{candidate}"
        return builtin, f"builtin (empty override {candidate})"

    return builtin, "builtin"


def get_dns_check_domains() -> list[str]:
    """Домены HTTPS-целей плюс дополнительные, проверяемые только по DNS."""
    domains: list[str] = []
    seen: set[str] = set()
    for host in (
        [host_of(target["value"]) for target in HTTPS_TARGETS]
        + list(DNS_EXTRA_DOMAINS)
    ):
        if host and host not in seen:
            seen.add(host)
            domains.append(host)
    return domains


# ---------------------------------------------------------------------------
# User custom domains (writable, persisted)
# ---------------------------------------------------------------------------

def load_user_domains() -> list[str]:
    """Load user-added custom domains from settings.json."""
    try:
        domains = settings_store.get_blockcheck_settings().get("user_domains", [])
        if not isinstance(domains, list):
            return []
        return [str(item).strip().lower() for item in domains if str(item).strip()]
    except Exception:
        return []


def save_user_domains(domains: list[str]) -> None:
    """Save user custom domains to settings.json."""
    try:
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for d in domains:
            d = d.strip().lower()
            if d and d not in seen:
                seen.add(d)
                unique.append(d)
        settings_store.set_blockcheck_settings({"user_domains": unique})
    except Exception as e:
        logger.warning("Failed to save user domains: %s", e)


def add_user_domain(domain: str) -> bool:
    """Add a domain to user list. Returns True if added (not duplicate)."""
    domain = host_of(domain)
    if not domain:
        return False
    # Голый googlevideo.com не проверяется напрямую; актуальный rr-хост
    # добавляется динамически в get_default_https_targets на каждом прогоне.
    if is_bare_googlevideo_host(domain):
        return False
    domains = load_user_domains()
    if domain in domains:
        return False
    domains.append(domain)
    save_user_domains(domains)
    return True


def remove_user_domain(domain: str) -> bool:
    """Remove a domain from user list. Returns True if removed."""
    domain = host_of(domain)
    domains = load_user_domains()
    if domain not in domains:
        return False
    domains.remove(domain)
    save_user_domains(domains)
    return True


# ---------------------------------------------------------------------------
# TCP 16-20KB targets
# ---------------------------------------------------------------------------

def load_tcp_targets_with_source(filepath: str | Path | None = None) -> tuple[list[dict], str]:
    """Цели проверки 16-20 КБ и источник, из которого они взяты."""
    builtin = [dict(target) for target in TCP_16_20_TARGETS]

    for candidate in _iter_override_candidates("tcp_16_20_targets.json", filepath):
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load tcp targets from %s: %s", candidate, e)
            return builtin, f"builtin (unreadable override {candidate})"

        if isinstance(data, list) and data:
            return data, f"file:{candidate}"
        return builtin, f"builtin (empty override {candidate})"

    return builtin, "builtin"


def load_tcp_targets(filepath: str | Path | None = None) -> list[dict]:
    """Load TCP 16-20KB test targets from JSON file."""
    targets, _ = load_tcp_targets_with_source(filepath)
    return targets


def select_tcp_targets(
    targets: list[dict],
    max_count: int = TCP_TARGET_MAX_COUNT,
    per_provider_cap: int = TCP_TARGETS_PER_PROVIDER,
) -> list[dict]:
    """Select a diverse subset of TCP targets.

    Selection strategy:
    1) round-robin across providers for fairness;
    2) up to ``per_provider_cap`` entries per provider;
    3) stop at ``max_count``.
    """
    if not targets or max_count <= 0:
        return []

    if per_provider_cap <= 0:
        per_provider_cap = 1

    by_provider: dict[str, list[dict]] = {}
    for t in targets:
        prov = str(t.get("provider") or "unknown")
        by_provider.setdefault(prov, []).append(t)

    provider_order = list(by_provider.keys())
    provider_taken = {provider: 0 for provider in provider_order}
    provider_index = {provider: 0 for provider in provider_order}

    selected: list[dict] = []
    while len(selected) < max_count:
        added_this_round = False

        for provider in provider_order:
            if provider_taken[provider] >= per_provider_cap:
                continue

            idx = provider_index[provider]
            items = by_provider[provider]
            if idx >= len(items):
                continue

            selected.append(items[idx])
            provider_index[provider] += 1
            provider_taken[provider] += 1
            added_this_round = True

            if len(selected) >= max_count:
                break

        if not added_this_round:
            break

    return selected


# ---------------------------------------------------------------------------
# Default targets
# ---------------------------------------------------------------------------

def get_default_https_targets(googlevideo_host: str | None = None) -> list[dict]:
    """HTTPS-цели, включая свежий GoogleVideo CDN только для текущего запуска."""
    targets = [dict(target) for target in HTTPS_TARGETS]
    dynamic_host = normalize_googlevideo_host(googlevideo_host)
    if not dynamic_host:
        return targets

    dynamic_target = {
        "name": "YouTube Video (*.googlevideo.com)",
        "value": f"https://{dynamic_host}",
    }
    redirector_index = next(
        (
            index for index, target in enumerate(targets)
            if host_of(target["value"]) == "redirector.googlevideo.com"
        ),
        len(targets),
    )
    targets.insert(redirector_index, dynamic_target)
    return targets


def get_default_https_targets_domains() -> list[str]:
    """Extract domain names from default HTTPS targets."""
    return [host_of(target["value"]) for target in HTTPS_TARGETS]


def get_default_stun_targets() -> list[dict]:
    """Default STUN/UDP targets.

    Note: stun.discord.gg no longer resolves via DNS on many ISPs.
    Discord voice uses media servers directly, not public STUN.
    """
    return [dict(target) for target in STUN_TARGETS]


def get_default_ping_targets() -> list[dict]:
    """Default ICMP ping targets."""
    return [dict(target) for target in PING_TARGETS]


def get_all_default_targets(googlevideo_host: str | None = None) -> list[dict]:
    """Get all default targets combined."""
    return (
        get_default_https_targets(googlevideo_host)
        + get_default_stun_targets()
        + get_default_ping_targets()
    )


def build_targets_with_user_domains(
    extra_domains: list[str] | None = None,
    *,
    googlevideo_host: str | None = None,
) -> list[dict]:
    """Build full target list: defaults + user domains + extra domains."""
    targets = get_all_default_targets(googlevideo_host)

    # Load persisted user domains
    user_domains = load_user_domains()

    # Merge extra domains (from GUI input)
    if extra_domains:
        for d in extra_domains:
            d = host_of(d)
            if d and d not in user_domains:
                user_domains.append(d)

    # Convert user domains to HTTPS targets, skip duplicates
    existing_hosts = {
        host_of(t["value"]) for t in targets if not is_pseudo_target(t["value"])
    }

    for domain in user_domains:
        # Сохранённый ранее голый googlevideo.com: rr-хост уже добавлен
        # динамически выше, а apex всегда даёт ложную «блокировку».
        if is_bare_googlevideo_host(domain):
            continue
        if domain not in existing_hosts:
            # Use domain as display name (capitalize first letter)
            name = domain.split(".")[0].capitalize() if "." in domain else domain
            targets.insert(-5, {"name": f"[U] {name}", "value": f"https://{domain}"})
            existing_hosts.add(domain)

    return targets
