"""The entry page of Zapret Links comes from the server's list; the built-in address is only the fallback.

The report (2026-11-22): the BlockCheck button "Как это работает?" opened a Google "unable to open the file" page, because the
entry page had been redeployed and the address built into the program answered 404 (the release posts were right, they read
the address from ``/_sites/links.json``)."""

from __future__ import annotations

import pytest

from zapret_update import mirrors

CURRENT = "https://script.google.com/macros/s/NEWID/exec"


@pytest.fixture(autouse=True)
def _clean_templates():
    saved = dict(mirrors._entry_templates)
    mirrors._entry_templates.clear()
    yield
    mirrors._entry_templates.clear()
    mirrors._entry_templates.update(saved)


def _document(**templates):
    return {"schema": 1, "families": {family: {"names": [], "templates": {"entry": template}} for family, template in templates.items()}}


def test_the_link_uses_the_template_the_server_published_for_each_family() -> None:
    mirrors.learn_entry_templates(_document(git=f"{CURRENT}?s=git&p={{path}}", wiki=f"{CURRENT}?s=wiki&p={{path}}"))
    assert mirrors.entry_page_url("Zapret/Blockcheck", "wiki") == f"{CURRENT}?s=wiki&p=Zapret/Blockcheck"
    assert mirrors.entry_page_url("zapretdiscordyoutube/zapretgui/releases/tag/1", "git") == f"{CURRENT}?s=git&p=zapretdiscordyoutube/zapretgui/releases/tag/1"
    assert mirrors.entry_page_for("https://wiki.zapret.moe/Zapret/Blockcheck") == f"{CURRENT}?s=wiki&p=Zapret/Blockcheck"


def test_a_list_read_for_the_names_also_teaches_the_template() -> None:
    mirrors.ordered_hosts(_document(git=f"{CURRENT}?s=git&p={{path}}"), "git")
    assert mirrors.entry_page_url("a/b", "git") == f"{CURRENT}?s=git&p=a/b"


def test_without_a_list_the_built_in_address_is_used_and_it_is_not_the_dead_one() -> None:
    assert mirrors.entry_page_url("Zapret/Blockcheck", "wiki") == mirrors.ENTRY_PAGE + "?s=wiki&p=Zapret/Blockcheck"
    assert "AKfycbzcEPk67" not in mirrors.ENTRY_PAGE  # that deployment answers 404 since 2026-11-22


@pytest.mark.parametrize(
    "bad",
    [
        "http://script.google.com/x?p={path}",  # not https
        "https://x.example/e?p={path}{path}",  # two places for the path
        "https://x.example/e?p=",  # no place for the path
        "https://x.example/e p={path}",  # a space
        'https://x.example/e?p={path}"onmouseover=',  # a quote
        "javascript:alert({path})",
        "https://x.example/" + "a" * 600 + "?p={path}",  # too long
        12345,
    ],
)
def test_a_template_that_does_not_look_like_a_safe_link_is_ignored(bad) -> None:
    mirrors.learn_entry_templates(_document(git=f"{CURRENT}?s=git&p={{path}}"))
    mirrors.learn_entry_templates(_document(git=bad))
    assert mirrors.entry_page_url("x", "git") == f"{CURRENT}?s=git&p=x"  # the earlier good one stays


def test_garbage_documents_do_not_break_anything() -> None:
    for document in (None, [], {}, {"families": []}, {"families": {"git": None}}, {"families": {"git": {"templates": 5}}}):
        mirrors.learn_entry_templates(document)
    assert mirrors.entry_page_url("x", "git").startswith(mirrors.ENTRY_PAGE)
