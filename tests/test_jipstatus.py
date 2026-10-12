import datetime
import logging
import runpy
import sys
from html.parser import HTMLParser
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
    logins = []

    def run_main(jira, *argv):
        monkeypatch.setattr(sys, "argv", ["jipstatus", *argv])
        monkeypatch.setattr(cfg, "initiate_config", lambda: None)

        def login(use_test_server):
            logins.append(use_test_server)
            return jira, "me@linaro.org"

        monkeypatch.setattr(jiralogin, "get_jira_instance", login)
        jipstatus.main()
        return capsys.readouterr().out

    run_main.logins = logins
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
            FakeIssue("LAA-1", components=["kernel"]),
            FakeIssue("LAA-2", assignee=None),
        ]
    )

    pendings = list(jipstatus.enumerate_pending(jira))

    assert pendings == [
        {
            "issue": "LAA-1",
            "assignee": "Anders",
            "summary": "Fix it",
            "components": ["kernel"],
        },
        {
            "issue": "LAA-2",
            "assignee": "Unassigned",
            "summary": "Fix it",
            "components": [],
        },
    ]
    assert jira.calls[0][2]["fields"] == "summary,assignee,components"
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


def test_days_must_be_a_number(capsys):
    parser = jipstatus.get_parser()

    assert parser.parse_args(["--days", "3"]).days == 3
    with pytest.raises(SystemExit) as e:
        parser.parse_args(["--days", "1w"])

    assert e.value.code == 2
    assert "argument --days: invalid int value: '1w'" in capsys.readouterr().err


class TagChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.open_tags = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag != "br":
            self.open_tags.append(tag)

    def handle_endtag(self, tag):
        if not self.open_tags or self.open_tags.pop() != tag:
            self.errors.append(f"unexpected </{tag}>")


def html_errors(html):
    checker = TagChecker()
    checker.feed(html)
    return checker.errors + [f"<{tag}> not closed" for tag in checker.open_tags]


@pytest.mark.parametrize(
    "updates, pendings",
    [
        ([FakeIssue("LAA-1", created_age=1)], []),
        (
            [
                FakeIssue("LAA-1", comments=[fake_comment("a", 1)]),
                FakeIssue(
                    "LAA-2", comments=[fake_comment("b", 1), fake_comment("c", 1)]
                ),
            ],
            [],
        ),
        ([], [FakeIssue("LAA-3"), FakeIssue("LAA-4", assignee=None)]),
        (
            [FakeIssue("LAA-1", comments=[fake_comment("a\nb", 1)])],
            [FakeIssue("LAA-2"), FakeIssue("LAA-3", assignee="Bob")],
        ),
    ],
)
def test_main_html_tags_are_balanced(run_main, tmp_path, updates, pendings):
    path = tmp_path / "status.html"

    run_main(
        FakeJira(updates=updates, pendings=pendings), "-p", "laa", "--html", str(path)
    )

    assert html_errors(path.read_text()) == []


def test_main_html_lists_pending_and_components(run_main, tmp_path):
    path = tmp_path / "status.html"
    jira = FakeJira(
        pendings=[FakeIssue("LAA-2", summary="Ongoing", components=["kernel"])]
    )

    run_main(jira, "-p", "laa", "--html", str(path))

    html = path.read_text()
    assert "<li>Ongoing</li>" in html
    assert (
        '<li>[<a href="https://linaro.atlassian.net/browse/LAA-2">LAA-2</a>]'
        " [kernel] Ongoing</li>" in html
    )


def test_main_html_default_file_name(run_main, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    run_main(FakeJira(pendings=[FakeIssue("LAA-2")]), "-p", "laa", "--html")

    assert "LAA-2" in (tmp_path / "status.html").read_text()


def test_main_text_report_several_comments(run_main):
    issue = FakeIssue(
        "LAA-1",
        created_age=1,
        comments=[fake_comment("first", 2), fake_comment("second\nline", 1)],
    )

    out = run_main(FakeJira(updates=[issue]), "-p", "laa")

    assert out == (
        "\nAnders:\n"
        " * Past\n"
        "   * [LAA-1] Fix it - was created\n"
        "     * first\n"
        "     * second\n"
        "       line\n"
        "\n"
    )


def test_main_users_and_test_server(run_main):
    jira = FakeJira()

    run_main(jira, "--test", "-u", "anders", "-u", "bob")

    assert run_main.logins == [True]
    assert jira.calls[0][1].startswith(
        "(assignee = 'anders@linaro.org' or assignee = 'bob@linaro.org')"
    )


def test_enumerate_updates_keeps_comment_order(set_args):
    set_args("-p", "laa")
    issue = FakeIssue(comments=[fake_comment("one", 3), fake_comment("two", 1)])

    [update] = jipstatus.enumerate_updates(FakeJira(updates=[issue]))

    assert update["comments"] == ["one", "two"]


@pytest.mark.parametrize("argv, level", [([], logging.ERROR), (["-v"], logging.DEBUG)])
def test_initialize_logger(monkeypatch, argv, level):
    called = {}
    monkeypatch.setattr(jipstatus.log, "basicConfig", lambda **kw: called.update(kw))

    jipstatus.initialize_logger(jipstatus.get_parser().parse_args(argv))

    assert called["level"] == level


def test_module_runs_main(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["jipstatus", "--version"])
    monkeypatch.delitem(sys.modules, "jipdate.jipstatus")

    with pytest.raises(SystemExit) as e:
        runpy.run_module("jipdate.jipstatus", run_name="__main__")

    assert e.value.code == 0
    assert capsys.readouterr().out.startswith("jipstatus, ")
