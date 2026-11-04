"""Путь в @config пробы: winws2 разбирает файл через wordexp (съедает «\\» и режет по пробелу)."""

from __future__ import annotations

import ntpath
import shlex

import pytest

from blockcheck.strategy_search import environment
from blockcheck.strategy_search.environment import config_path_value


@pytest.fixture
def windows_paths(monkeypatch):
    monkeypatch.setattr(environment.os, "path", ntpath)


def test_path_inside_root_is_relative_with_forward_slashes(windows_paths):
    value = config_path_value(r"D:\Programs\Zapret\lists\ipset-all.txt", r"D:\Programs\Zapret")
    assert value == "lists/ipset-all.txt"


def test_root_with_space_stays_one_word_after_wordexp(windows_paths):
    value = config_path_value(r"D:\My Programs\Zapret\lists\ipset-all.txt", r"D:\My Programs\Zapret")
    assert shlex.split(value) == ["lists/ipset-all.txt"]


def test_path_outside_root_is_absolute_without_backslashes(windows_paths):
    value = config_path_value(r"E:\Other dir\ipset.txt", r"D:\Programs\Zapret")
    assert "\\" not in value
    assert shlex.split(value) == ["E:/Other dir/ipset.txt"]


def test_already_relative_path_is_kept(windows_paths):
    assert config_path_value("lists/ipset-roblox.txt", r"D:\Programs\Zapret") == "lists/ipset-roblox.txt"
