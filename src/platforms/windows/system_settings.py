from __future__ import annotations


class WindowsSystemSettings:
    """The Windows registry questions. ``winreg`` is imported inside the methods (it does not exist elsewhere)."""

    def accent_color(self) -> str | None:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Accent",
                0,
                winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AccentColorMenu")
                r = value & 0xFF
                g = (value >> 8) & 0xFF
                b = (value >> 16) & 0xFF
                return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return None

    def url_scheme_handler_registered(self, scheme: str) -> bool:
        """``HKEY_CLASSES_ROOT\\<scheme>\\shell\\open\\command`` exists: without it opening the link shows the "choose an app" window."""
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{scheme}\shell\open\command"):
                return True
        except Exception:
            return False

    def default_browser_id(self) -> str:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "ProgId")
            return str(value)
        except (OSError, ImportError):
            return ""

    def proxy_settings(self) -> tuple[str, str]:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        ) as key:

            def value(name: str):
                try:
                    return winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    return ""

            server = str(value("ProxyServer") or "") if value("ProxyEnable") else ""
            return server, str(value("AutoConfigURL") or "")
