"""DNS package.

Корневой пакет оставляем тонким: код берёт зависимости напрямую из
конкретных модулей (`dns.system_dns`, `dns.adapters`, `dns.runtime`,
`dns.dns_providers` и т.д.), а не через общий реэкспорт.
"""
