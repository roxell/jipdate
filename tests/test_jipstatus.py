import datetime
import sys
from types import SimpleNamespace

import pytest

from jipdate import cfg, jipstatus, jiralogin
from jipdate.adf import text_to_adf


def days_ago(days):
    when = datetime.datetime.now() - datetime.timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def fake_comment(body, age):
    return SimpleNamespace(created=days_ago(age), raw={"body": body})


def fake_history(age, field, to_string):
    item = SimpleNamespace(field=field, toString=to_string)
    return SimpleNamespace(created=days_ago(age), items=[item])


class FakeIssue:
    def __init__(
        self,
        key="LAA-1",
        summary="Fix it",
        assignee="Anders",
        components=(),
        created_age=30,
        comments=(),
        histories=(),
    ):
        self.key = key
        self.fields = SimpleNamespace(
            summary=summary,
            assignee=SimpleNamespace(displayName=assignee) if assignee else None,
            components=list(components),
            created=days_ago(created_age),
            comment=SimpleNamespace(comments=list(comments)),
        )
        self.changelog = SimpleNamespace(histories=list(histories))

    def __str__(self):
        return self.key


class FakeJira:
    def __init__(self, updates=(), pendings=()):
        self.updates = list(updates)
        self.pendings = list(pendings)
        self.calls = []

    def search_issues(self, jql, **kwargs):
        self.calls.append(("search_issues", jql, kwargs))
        if "status = 'In Progress'" in jql:
            return self.pendings
        return self.updates

    def client_info(self):
        return "https://linaro.atlassian.net"


@pytest.fixture
def set_args(monkeypatch):
    def set_args(*argv):
        monkeypatch.setattr(cfg, "args", jipstatus.get_parser().parse_args(argv))

    return set_args


@pytest.fixture
def run_main(monkeypatch, capsys):
    def run_main(jira, *argv):
        monkeypatch.setattr(sys, "argv", ["jipstatus", *argv])
        monkeypatch.setattr(cfg, "initiate_config", lambda: None)
        monkeypatch.setattr(
            jiralogin, "get_jira_instance", lambda test: (jira, "me@linaro.org")
        )
        jipstatus.main()
        return capsys.readouterr().out

    return run_main


@pytest.mark.parametrize(
    "argv, jql",
    [
        (["-u", "anders"], "(assignee = 'anders@linaro.org')"),
        (
            ["-u", "anders", "-u", "bob@example.org"],
            "(assignee = 'anders@linaro.org' or assignee = 'bob@example.org')",
        ),
        (["-p", "laa"], "project =  'LAA' "),
        (["-t", "My-Team"], "assignee in membersOf('my-team') "),
        (
            ["-p", "laa", "-t", "my-team"],
            "(project = LAA or assignee in membersOf('my-team')) ",
        ),
    ],
)
def test_default_jql(set_args, argv, jql):
    set_args(*argv)
    assert jipstatus.default_jql() == jql


def test_enumerate_updates_search(set_args):
    set_args("-p", "laa", "--days", "3")
    jira = FakeJira()

    list(jipstatus.enumerate_updates(jira))

    assert jira.calls == [
        (
            "search_issues",
            "project =  'LAA' AND updatedDate > -3d",
            {
                "expand": "changelog",
                "fields": "summary,comment,components,assignee,created",
                "maxResults": False,
            },
        )
    ]


@pytest.mark.parametrize("body", ["recent\ntext", text_to_adf("recent\ntext")])
def test_enumerate_updates_recent_comments(set_args, body):
    set_args("-p", "laa")
    issue = FakeIssue(
        components=["kernel"],
        comments=[fake_comment("old", 10), fake_comment(body, 1)],
    )

    updates = list(jipstatus.enumerate_updates(FakeJira(updates=[issue])))

    assert updates == [
        {
            "issue": "LAA-1",
            "assignee": "Anders",
            "summary": "Fix it",
            "comments": ["recent\ntext"],
            "resolution": None,
            "components": ["kernel"],
        }
    ]


def test_enumerate_updates_skips_issue_without_news(set_args):
    set_args("-p", "laa")
    issue = FakeIssue(
        comments=[fake_comment("old", 10)],
        histories=[fake_history(10, "resolution", "Fixed")],
    )

    assert list(jipstatus.enumerate_updates(FakeJira(updates=[issue]))) == []


def test_enumerate_updates_new_issue_is_created(set_args):
    set_args("-p", "laa")
    issue = FakeIssue(created_age=2, assignee=None)

    [update] = jipstatus.enumerate_updates(FakeJira(updates=[issue]))

    assert update["resolution"] == "Created"
    assert update["assignee"] == "Unassigned"


def test_enumerate_updates_recent_resolution(set_args):
    set_args("-p", "laa")
    issue = FakeIssue(
        histories=[
            fake_history(2, "status", "Done"),
            fake_history(1, "resolution", "Fixed"),
        ]
    )

    [update] = jipstatus.enumerate_updates(FakeJira(updates=[issue]))

    assert update["resolution"] == "Fixed"


def test_enumerate_updates_days_option(set_args):
    set_args("-p", "laa", "--days", "14")
    issue = FakeIssue(comments=[fake_comment("ten days ago", 10)])

    [update] = jipstatus.enumerate_updates(FakeJira(updates=[issue]))

    assert update["comments"] == ["ten days ago"]


def test_enumerate_pending(set_args):
    set_args("-p", "laa")
    jira = FakeJira(
        pendings=[
            FakeIssue("LAA-1", created_age=2),
            FakeIssue("LAA-2", created_age=30, assignee=None),
        ]
    )

    pendings = list(jipstatus.enumerate_pending(jira))

    assert [(p["issue"], p["assignee"], p["new"]) for p in pendings] == [
        ("LAA-1", "Anders", True),
        ("LAA-2", "Unassigned", False),
    ]
    jql = jira.calls[0][1]
    assert jql.startswith("project =  'LAA' AND status = 'In Progress'")
    assert "AND issuetype != Initiative" in jql
    assert "AND issuetype != Epic" in jql


def test_main_uses_login_name_without_user(run_main):
    jira = FakeJira()

    run_main(jira)

    assert jira.calls[0][1].startswith("(assignee = 'me@linaro.org')")


def test_main_text_report(run_main):
    jira = FakeJira(
        updates=[
            FakeIssue(
                "LAA-1",
                components=["kernel", "tools"],
                comments=[fake_comment("first\nsecond", 1)],
                histories=[fake_history(1, "resolution", "Fixed")],
            )
        ],
        pendings=[
            FakeIssue("LAA-2", summary="Ongoing work"),
            FakeIssue("LAA-3", summary="Nobody", assignee=None),
        ],
    )

    out = run_main(jira, "-p", "laa")

    assert out == (
        "\nAnders:\n"
        " * Past\n"
        "   * [LAA-1] [kernel,tools] Fix it - was fixed\n"
        "     * first\n"
        "       second\n"
        " * Ongoing\n"
        "   * [LAA-2] Ongoing work\n"
        "\nUnassigned:\n"
        " * Ongoing\n"
        "   * [LAA-3] Nobody\n"
        "\n"
    )


def test_main_sorts_assignees_with_unassigned_last(run_main):
    jira = FakeJira(
        pendings=[
            FakeIssue("LAA-1", assignee=None),
            FakeIssue("LAA-2", assignee="Zed"),
            FakeIssue("LAA-3", assignee="Anna"),
        ]
    )

    out = run_main(jira, "-p", "laa")

    assert out.index("Anna:") < out.index("Zed:") < out.index("Unassigned:")


def test_main_html_report(run_main, tmp_path):
    path = tmp_path / "status.html"
    jira = FakeJira(
        updates=[
            FakeIssue(
                "LAA-1",
                summary="a < b & c",
                comments=[fake_comment("line1\n<script>x</script>", 1)],
            )
        ]
    )

    run_main(jira, "-p", "laa", "--html", str(path))

    html = path.read_text()
    assert (
        '[<a href="https://linaro.atlassian.net/browse/LAA-1">LAA-1</a>]'
        " a &lt; b &amp; c" in html
    )
    assert "<li>line1<br>&lt;script&gt;x&lt;/script&gt;</li>" in html
    assert "<script>" not in html
