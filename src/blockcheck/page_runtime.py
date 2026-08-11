from __future__ import annotations

from dataclasses import dataclass

from support_request_actions import prepare_blockcheck_support_request


@dataclass(frozen=True, slots=True)
class BlockcheckPageInitialStatePlan:
    user_domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UserDomainRejection:
    """Домен отклонён по существу, а не как дубликат — UI объясняет причину."""

    domain: str
    reason: str = "googlevideo_apex"


def _normalize_user_domains(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(str(item).strip().lower() for item in values if str(item).strip())


def load_page_initial_state() -> BlockcheckPageInitialStatePlan:
    try:
        from settings.store import read_settings

        data = read_settings()
        blockcheck = dict(data.get("blockcheck") or {})
        domains = _normalize_user_domains(blockcheck.get("user_domains"))
    except Exception:
        domains = ()
    return BlockcheckPageInitialStatePlan(user_domains=domains)


def load_user_domains() -> list[str]:
    from blockcheck.targets import load_user_domains

    return list(load_user_domains())

def add_user_domain(text: str) -> str | UserDomainRejection | None:
    from blockcheck.googlevideo_discovery import is_bare_googlevideo_host
    from blockcheck.hosts import host_of
    from blockcheck.targets import add_user_domain

    normalized = host_of(text)
    if is_bare_googlevideo_host(normalized):
        return UserDomainRejection(domain=normalized)
    if not add_user_domain(text):
        return None
    return normalized

def remove_user_domain(domain: str) -> None:
    from blockcheck.targets import remove_user_domain

    remove_user_domain(domain)


def prepare_support(*, run_log_file: str | None, mode_label: str, extra_domains: list[str]):
    return prepare_blockcheck_support_request(
        run_log_file=run_log_file,
        mode_label=mode_label,
        extra_domains=extra_domains,
    )
