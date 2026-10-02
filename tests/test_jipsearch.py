import logging
import runpy
import sys
from types import SimpleNamespace

import pytest
from jira import JIRAError

from jipdate import cfg, jipsearch, jiralogin
from jipdate.adf import text_to_adf

BROWSE = "https://linaro.atlassian.net/browse"


def raw_issue(key="LAA-1", assignee=None, **fields):
    raw = {
        "key": key,
        "fields": {
            "summary": " Fix it ",
            "created": "2026-10-01T10:00:00.000+0000",
            "issuetype": {"name": "Task"},
            "status": {"statusCategory": {"name": "In Progress"}},
            "assignee": assignee,
            "description": None,
            "timetracking": {},
        },
    }
    raw["fields"].update(fields)
    return raw


ANDERS = {"displayName": "Anders", "emailAddress": "anders@linaro.org"}


def fake_comment(body, updated="2026-10-02", author="Someone"):
    return SimpleNamespace(updated=updated, author=author, raw={"body": body})


class FakeJira:
    def __init__(self, issues=(), comments=(), users=None, error=None):
        self.issues = [SimpleNamespace(raw=issue) for issue in issues]
        self._comments = list(comments)
        self.users = users or {}
        self.error = error
        self.calls = []

    def search_issues(self, jql, **kwargs):
        self.calls.append(("search_issues", jql, kwargs))
        if self.error:
            raise self.error
        return self.issues

    def search_users(self, query):
        return [SimpleNamespace(accountId=i) for i in self.users.get(query, [])]

    def comments(self, key):
        return self._comments


@pytest.fixture
def set_args(monkeypatch):
    def set_args(*argv):
        monkeypatch.setattr(cfg, "args", jipsearch.get_parser().parse_args(argv))

    return set_args


@pytest.mark.parametrize(
    "argv, jql",
    [
        ([], ""),
        (["-k", "LAA-1", "-k", "LAA-2"], "key in (LAA-1, LAA-2)"),
        (["-p", "LAA,TCWG"], "project in (LAA,TCWG)"),
        (["-s", "Sprint 1"], "sprint in ('Sprint 1')"),
        (["-e", "LAA-5"], "(key = LAA-5 or parentepic = LAA-5)"),
        (["-ca", "2026-01-01"], "created >= 2026-01-01"),
        (["--created-before=-5d"], "created <= -5d"),
        (["-ua", "2026-01-01"], "updated >= 2026-01-01"),
        (["--updated-before=-1w"], "updated <= -1w"),
        (
            ["-p", "LAA", "--updated-after=-1w"],
            "project in (LAA) AND updated >= -1w",
        ),
    ],
)
def test_create_jql_options(set_args, argv, jql):
    set_args(*argv)
    assert jipsearch.create_jql(FakeJira(), None) == jql


def test_create_jql_starts_with_given_jql(set_args):
    set_args("-p", "LAA")
    jql = jipsearch.create_jql(FakeJira(), "status = Done")
    assert jql == "status = Done AND project in (LAA)"


def test_create_jql_reporters_are_account_ids(set_args):
    set_args("-r", "anders,bob")
    jira = FakeJira(users={"anders": ["id-a"], "bob": ["id-b1", "id-b2"]})

    assert jipsearch.create_jql(jira, "") == "reporter in (id-a,id-b1,id-b2)"


def test_create_jql_assignees_are_account_ids(set_args):
    set_args("-a", "anders", "-a", "bob")
    jira = FakeJira(users={"anders": ["id-a"], "bob": ["id-b"]})

    assert jipsearch.create_jql(jira, "") == "assignee in (id-a,id-b)"


def test_create_jql_unknown_user_adds_nothing(set_args):
    set_args("-r", "nobody", "-a", "nobody")
    assert jipsearch.create_jql(FakeJira(), "") == ""


def test_search_issues_fetches_all_pages_and_returns_raw(set_args):
    set_args()
    issue = raw_issue()
    jira = FakeJira(issues=[issue])

    assert jipsearch.search_issues(jira, "project = LAA") == [issue]
    assert jira.calls == [
        (
            "search_issues",
            "project = LAA",
            {
                "maxResults": False,
                "fields": [
                    "summary",
                    "description",
                    "created",
                    "status",
                    "issuetype",
                    "assignee",
                    "timetracking",
                ],
            },
        )
    ]


def test_search_issues_adds_format_and_parent_fields(set_args):
    set_args("-f", "{key} {reporter:displayName} {labels}", "-pk")
    jira = FakeJira()

    jipsearch.search_issues(jira, "")

    assert jira.calls[0][2]["fields"][-4:] == ["key", "reporter", "labels", "parent"]


def test_search_issues_exits_on_jira_error(set_args, capsys):
    set_args()
    jira = FakeJira(error=JIRAError(text="Error in the JQL query"))

    with pytest.raises(SystemExit) as e:
        jipsearch.search_issues(jira, "bad jql")

    assert e.value.code == 1
    assert capsys.readouterr().out == "Error in the JQL query\n"


def test_call_jqls_joins_results(set_args):
    set_args("-p", "LAA")
    jira = FakeJira(issues=[raw_issue()])

    issues = jipsearch.call_jqls(jira, ["status = Done", "status = Open"])

    assert len(issues) == 2
    assert [call[1] for call in jira.calls] == [
        "status = Done AND project in (LAA)",
        "status = Open AND project in (LAA)",
    ]


def test_print_issues_default_line(set_args, capsys):
    set_args()

    jipsearch.print_issues(FakeJira(), [raw_issue(assignee=ANDERS)])

    assert capsys.readouterr().out == (
        f"{BROWSE}/LAA-1 , Type: Task, Summary: Fix it , Created: 2026-10-01"
        " , Status: In Progress, Assignee: Anders,"
        " Assignee email: anders@linaro.org\n"
    )


def test_print_issues_without_assignee(set_args, capsys):
    set_args()

    jipsearch.print_issues(FakeJira(), [raw_issue()])

    assert "Assignee" not in capsys.readouterr().out


def test_print_issues_parent(set_args, capsys):
    set_args("-pk")

    jipsearch.print_issues(FakeJira(), [raw_issue(parent={"key": "LAA-5"})])

    assert capsys.readouterr().out.endswith(f", parent: {BROWSE}/LAA-5\n")


def test_print_issues_parent_missing(set_args, capsys):
    set_args("-pk")

    jipsearch.print_issues(FakeJira(), [raw_issue()])

    assert capsys.readouterr().out == "No key 'parent'.\n"


@pytest.mark.parametrize("description", ["one\ntwo", text_to_adf("one\ntwo")])
def test_print_issues_description(set_args, capsys, description):
    set_args("-d")

    jipsearch.print_issues(FakeJira(), [raw_issue(description=description)])

    out = capsys.readouterr().out
    assert out.split("\n", 1)[1] == "# Description:\n#   one\n#   two\n#\n\n"


def test_print_issues_empty_description(set_args, capsys):
    set_args("-d")

    jipsearch.print_issues(FakeJira(), [raw_issue()])

    assert capsys.readouterr().out.endswith("# Description:\n")


@pytest.mark.parametrize("body", ["new\ntext", text_to_adf("new\ntext")])
def test_print_issues_last_comment(set_args, capsys, body):
    set_args("-c")
    jira = FakeJira(
        comments=[
            fake_comment("old", updated="2026-10-01", author="First"),
            fake_comment(body, updated="2026-10-05", author="Last"),
        ]
    )
    issue = raw_issue(
        assignee=ANDERS,
        timetracking={"timeSpent": "2h", "originalEstimate": "1d"},
    )

    jipsearch.print_issues(jira, [issue])

    out = capsys.readouterr().out
    assert out.split("\n", 1)[1] == (
        "# Assignee: Anders, Original Estimate: 1d, Time Spent: 2h\n"
        "# Last comment, updated: 2026-10-05, by: Last\n"
        "# ---8<---\n# new\n# text\n# --->8---\n\n"
    )


def test_print_issues_last_comment_without_assignee_or_time(set_args, capsys):
    set_args("-c")
    jira = FakeJira(comments=[fake_comment("hello")])

    jipsearch.print_issues(jira, [raw_issue()])

    out = capsys.readouterr().out
    assert "# Assignee: Unassigned, Original Estimate: Not Set, Time Spent: 0\n" in out


def test_print_issues_no_comments(set_args, capsys):
    set_args("-c")

    jipsearch.print_issues(FakeJira(), [raw_issue()])

    assert "#" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "fmt, out",
    [
        ("{key}", "LAA-1"),
        ("{key}: {summary}", "LAA-1:  Fix it "),
        ("{assignee:emailAddress}", "anders@linaro.org"),
        ("{parent:key}", "None"),
        ("{labels}", "None"),
    ],
)
def test_print_issues_format(set_args, capsys, fmt, out):
    set_args("-f", fmt)

    jipsearch.print_issues(FakeJira(), [raw_issue(assignee=ANDERS)])

    assert capsys.readouterr().out == out + "\n"


def test_print_issues_format_with_empty_field(set_args, capsys):
    set_args("-f", "{key} {assignee:emailAddress}")

    jipsearch.print_issues(FakeJira(), [raw_issue()])

    assert capsys.readouterr().out == "LAA-1 None\n"


def test_print_issues_format_nested_value(set_args, capsys):
    set_args("-f", "{key} -> {parent:key} {status:statusCategory:name}")
    issue = raw_issue(parent={"key": "LAA-5"})

    jipsearch.print_issues(FakeJira(), [issue])

    assert capsys.readouterr().out == "LAA-1 -> LAA-5 In Progress\n"


def test_print_issues_format_key_inside_field(set_args, capsys):
    set_args("-f", "{key} {issuetype:name}")

    jipsearch.print_issues(FakeJira(), [raw_issue()])

    assert capsys.readouterr().out == "LAA-1 Task\n"


def test_print_issues_several_issues(set_args, capsys):
    set_args("-f", "{key}")

    jipsearch.print_issues(FakeJira(), [raw_issue("LAA-1"), raw_issue("LAA-2")])

    assert capsys.readouterr().out == "LAA-1\nLAA-2\n"


@pytest.mark.parametrize("argv, level", [([], logging.ERROR), (["-v"], logging.DEBUG)])
def test_initialize_logger(monkeypatch, argv, level):
    called = {}
    monkeypatch.setattr(jipsearch.log, "basicConfig", lambda **kw: called.update(kw))

    jipsearch.initialize_logger(jipsearch.get_parser().parse_args(argv))

    assert called["level"] == level


@pytest.fixture
def run_main(monkeypatch, capsys):
    logins = []
    monkeypatch.setattr(cfg, "initiate_config", lambda: None)

    def run_main(jira, *argv):
        monkeypatch.setattr(sys, "argv", ["jipsearch", *argv])

        def login(use_test_server):
            logins.append(use_test_server)
            return jira, "me"

        monkeypatch.setattr(jiralogin, "get_jira_instance", login)
        jipsearch.main()
        return capsys.readouterr().out

    run_main.logins = logins
    return run_main


def test_main_without_arguments_prints_help(run_main, capsys):
    with pytest.raises(SystemExit) as e:
        run_main(FakeJira())

    assert e.value.code == 1
    assert capsys.readouterr().err.startswith("usage: jipsearch")
    assert run_main.logins == []


def test_main_with_jql(run_main):
    jira = FakeJira(issues=[raw_issue()])

    out = run_main(jira, "-j", "status = Done", "-p", "LAA", "-f", "{key}")

    assert out == "LAA-1\n"
    assert [call[1] for call in jira.calls] == ["status = Done AND project in (LAA)"]
    assert run_main.logins == [False]


def test_main_with_several_jql(run_main):
    jira = FakeJira(issues=[raw_issue()])

    out = run_main(jira, "-j", "status = Done", "-j", "status = Open", "-f", "{key}")

    assert out == "LAA-1\nLAA-1\n"
    assert [call[1] for call in jira.calls] == ["status = Done", "status = Open"]


def test_main_without_jql_uses_options(run_main):
    jira = FakeJira(issues=[raw_issue(assignee=ANDERS)])

    out = run_main(jira, "-k", "LAA-1")

    assert [call[1] for call in jira.calls] == ["key in (LAA-1)"]
    assert out.startswith(f"{BROWSE}/LAA-1 , Type: Task")


def test_module_runs_main(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["jipsearch", "--version"])
    monkeypatch.delitem(sys.modules, "jipdate.jipsearch")

    with pytest.raises(SystemExit) as e:
        runpy.run_module("jipdate.jipsearch", run_name="__main__")

    assert e.value.code == 0
    assert capsys.readouterr().out.startswith("jipsearch, ")
