from __future__ import annotations

import time

ERROR_SERVICE_DOES_NOT_EXIST = 1060


class WindowsServices:
    """Windows services through pywin32. It is imported inside the methods: loading it
    at start would slow the launch gate and could fail before any error window can show."""

    def is_running(self, name: str) -> bool:
        import win32service

        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            try:
                svc = win32service.OpenService(scm, name, win32service.SERVICE_QUERY_STATUS)
            except win32service.error as exc:
                if exc.winerror == ERROR_SERVICE_DOES_NOT_EXIST:
                    return False
                raise  # other errors go up to the caller
            try:
                return win32service.QueryServiceStatus(svc)[1] == win32service.SERVICE_RUNNING
            finally:
                win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)

    def start(self, name: str, timeout: float = 10) -> bool:
        try:
            import win32serviceutil

            win32serviceutil.StartService(name)

            started = time.time()
            while time.time() - started < timeout:
                if self.is_running(name):
                    return True
                time.sleep(0.5)
            return False
        except Exception as exc:
            # The service was already running.
            return "уже запущена" in str(exc) or "already running" in str(exc).lower()
