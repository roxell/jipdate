import logging
import os
import runpy
import sys
from types import SimpleNamespace

import pytest
from jira import JIRAError

from jipdate import cfg, jipdate, jiralogin
from jipdate.adf import text_to_adf


class FakeIssue:
    def __init__(self, key, summary="Summary", status="In Progress", issuetype="Task"):
        self.key = key
        self.fields = SimpleNamespace(
            summary=summary, status=status, issuetype=issuetype
        )

    def __str__(self):
        return self.key


class FakeJira:
    def __init__(self, issues=(), comments=(), issue_error=None):
        self.issues = list(issues)
        self._comments = list(comments)
        self.issue_error = issue_error
        self.calls = []

    def search_issues(self, jql, **kwargs):
        self.calls.append(("search_issues", jql, kwargs))
        return self.issues

    def issue(self, key):
        for issue in self.issues:
            if str(issue) == key:
                return issue
        raise self.issue_error or JIRAError(text="Issue Does Not Exist")

    def comments(self, issue):
        return self._comments

    def resolutions(self):
        return [
            SimpleNamespace(name="Fixed", id="1"),
            SimpleNamespace(name="Won't Do", id="2"),
        ]

    def transitions(self, issue):
        return [
            {"name": "In Progress", "id": "11"},
            {"name": "Resolved", "id": "21"},
        ]

    def transition_issue(self, issue, transition, **kwargs):
        self.calls.append(("transition_issue", str(issue), transition, kwargs))

    def add_comment(self, issue, body):
        self.calls.append(("add_comment", str(issue), body))

    def add_worklog(self, issue, timeSpent, comment):
        self.calls.append(("add_worklog", str(issue), timeSpent, comment))


def fake_comment(body):
    return SimpleNamespace(raw={"body": body})


@pytest.fixture
def set_args(monkeypatch):
    def set_args(*argv):
        monkeypatch.setattr(cfg, "args", jipdate.get_parser().parse_args(argv))

    return set_args


@pytest.fixture
def set_config(monkeypatch):
    def set_config(config):
        monkeypatch.setattr(cfg, "yml_config", config)

    set_config({})
    return set_config


@pytest.fixture
def answer(monkeypatch):
    def answer(reply):
        monkeypatch.setattr("builtins.input", lambda prompt: reply)

    answer("y")
    return answer


@pytest.fixture
def status_file(tmp_path):
    def status_file(text):
        path = tmp_path / "status.txt"
        path.write_text(text)
        return str(path)

    return status_file


def test_add_domain_appends_linaro_domain():
    assert jipdate.add_domain("first.last") == "first.last@linaro.org"


def test_add_domain_keeps_existing_domain():
    assert jipdate.add_domain("first.last@example.org") == "first.last@example.org"


def test_email_to_name():
    assert jipdate.email_to_name("first.last@linaro.org") == "First Last"


def test_open_editor_uses_editor_variable(monkeypatch):
    called = []
    monkeypatch.setenv("EDITOR", "vim -u NONE")
    monkeypatch.setattr(jipdate, "call", called.append)

    jipdate.open_editor("status.txt")

    assert called == [["vim", "-u", "NONE", "status.txt"]]


def test_open_file_with_name_opens_that_file(tmp_path):
    path = tmp_path / "status.txt"
    with jipdate.open_file(str(path)) as f:
        assert f.name == str(path)


def test_open_file_without_name_creates_temporary_file(tmp_path, monkeypatch):
    monkeypatch.setattr(jipdate.tempfile, "tempdir", str(tmp_path))
    with jipdate.open_file(None) as f:
        assert f.name.startswith(str(tmp_path))


def test_config_defaults_without_config_entries(set_config):
    assert jipdate.get_extra_comments() == "\n"
    assert jipdate.get_header() == ""
    assert jipdate.merge_issue_header() is False
    assert jipdate.get_header_separator() == " | "
    assert jipdate.get_editor() is True


def test_config_values_are_read(set_config):
    set_config(
        {
            "comments": ["# one", "# two"],
            "header": ["Hi,", "status:"],
            "use_combined_issue_header": True,
            "separator": " - ",
            "text-editor": False,
        }
    )

    assert jipdate.get_extra_comments() == "# one\n# two\n"
    assert jipdate.get_header() == "Hi,\nstatus:\n\n"
    assert jipdate.merge_issue_header() is True
    assert jipdate.get_header_separator() == " - "
    assert jipdate.get_editor() is False


def test_update_jira_adds_comment_as_adf():
    jira = FakeJira()

    jipdate.update_jira(jira, "LAA-1", "line 1\nline 2", {"transition": None})

    assert jira.calls == [("add_comment", "LAA-1", text_to_adf("line 1\nline 2"))]


def test_update_jira_adds_worklog_with_time_spent():
    jira = FakeJira()

    jipdate.update_jira(jira, "LAA-1", "done", {"transition": None}, "2h")

    assert jira.calls[1] == ("add_worklog", "LAA-1", "2h", text_to_adf("done"))


def test_update_jira_without_comment_does_nothing():
    jira = FakeJira()

    jipdate.update_jira(jira, "LAA-1", "", {"transition": None}, "2h")

    assert jira.calls == []


def test_update_jira_transition_without_resolution():
    jira = FakeJira()

    jipdate.update_jira(jira, "LAA-1", "", {"transition": "21", "resolution": None})

    assert jira.calls == [("transition_issue", "LAA-1", "21", {})]


def test_update_jira_transition_with_resolution():
    jira = FakeJira()

    jipdate.update_jira(jira, "LAA-1", "", {"transition": "21", "resolution": "1"})

    assert jira.calls == [
        ("transition_issue", "LAA-1", "21", {"fields": {"resolution": {"id": "1"}}})
    ]


def test_write_last_jira_comment_with_text_body(tmp_path):
    path = tmp_path / "out.txt"
    jira = FakeJira(comments=[fake_comment("old"), fake_comment("new\nlines")])

    with open(path, "w") as f:
        jipdate.write_last_jira_comment(f, jira, "LAA-1")

    assert path.read_text() == (
        "# Last comment:\n# ---8<---\n# new\n# lines\n# --->8---\n"
    )


def test_write_last_jira_comment_with_adf_body(tmp_path):
    path = tmp_path / "out.txt"
    jira = FakeJira(comments=[fake_comment(text_to_adf("from\nadf"))])

    with open(path, "w") as f:
        jipdate.write_last_jira_comment(f, jira, "LAA-1")

    assert "# from\n# adf\n" in path.read_text()


def test_write_last_jira_comment_without_comments(tmp_path):
    path = tmp_path / "out.txt"

    with open(path, "w") as f:
        jipdate.write_last_jira_comment(f, FakeJira(), "LAA-1")

    assert path.read_text() == ""


@pytest.mark.parametrize(
    "argv, jql",
    [
        (
            [],
            "issuetype in (Sub-task, Epic, Initiative, Story, Task, Sub-task, Bug)"
            ' AND assignee = currentUser() AND status in ("In Progress")',
        ),
        (
            ["-x"],
            "issuetype in (Sub-task, Epic, Initiative)"
            ' AND assignee = currentUser() AND status in ("In Progress")',
        ),
        (
            ["-e"],
            "issuetype in (Sub-task, Epic)"
            ' AND assignee = currentUser() AND status in ("In Progress")',
        ),
        (
            ["--all"],
            "issuetype in (Sub-task, Epic, Initiative, Story, Task, Sub-task, Bug)"
            " AND assignee = currentUser()"
            " AND status not in (Resolved, Closed, Completed)",
        ),
        (
            ["-e", "-u", "first.last"],
            'issuetype in (Sub-task, Epic) AND assignee = "first.last@linaro.org"'
            ' AND status in ("In Progress")',
        ),
    ],
)
def test_get_jira_issues_jql(tmp_path, set_args, set_config, argv, jql):
    set_args("-q", "-f", str(tmp_path / "status.txt"), *argv)
    jira = FakeJira()

    jipdate.get_jira_issues(jira, "first.last@linaro.org")

    assert jira.calls == [("search_issues", jql, {"maxResults": False})]


def test_get_jira_issues_writes_status_file(tmp_path, set_args, set_config):
    path = tmp_path / "status.txt"
    set_args("-q", "-f", str(path))
    set_config({"header": ["Hi,"], "comments": ["# extra"]})
    issue = FakeIssue("LAA-1", summary="Fix it", issuetype="Story")
    jira = FakeJira(issues=[issue])

    filename, issues = jipdate.get_jira_issues(jira, "first.last@linaro.org")

    assert filename == str(path)
    assert issues == [issue]
    subject, body = path.read_text().split("\n\n", 1)
    assert subject.startswith("Subject: [Weekly] Week ending ")
    assert body == (
        "Hi,\n\nFirst Last\n\n"
        "[LAA-1]\n# Header: Fix it\n# Type: Story\n# Status: In Progress\n"
        "# extra\n\n"
    )


def test_get_jira_issues_combined_issue_header(tmp_path, set_args, set_config):
    path = tmp_path / "status.txt"
    set_args("-q", "-f", str(path))
    set_config({"use_combined_issue_header": True, "separator": " - "})

    jipdate.get_jira_issues(FakeJira(issues=[FakeIssue("LAA-1", "Fix it")]), "me")

    assert "[LAA-1 - Fix it]\n# Type:" in path.read_text()


def test_get_jira_issues_with_last_comment(tmp_path, set_args, set_config):
    path = tmp_path / "status.txt"
    set_args("-q", "-l", "-f", str(path))
    jira = FakeJira(issues=[FakeIssue("LAA-1")], comments=[fake_comment("last")])

    jipdate.get_jira_issues(jira, "me")

    assert "# Last comment:\n# ---8<---\n# last\n" in path.read_text()


def test_parse_status_file_adds_comment(set_args, set_config, answer, status_file):
    set_args("-s")
    issue = FakeIssue("LAA-1")
    jira = FakeJira()
    path = status_file(
        "Subject: [Weekly] Week ending 2026-10-22\n\n"
        "[LAA-1]\n# Header: Fix it\n\nfirst line\nsecond line\n\n"
    )

    jipdate.parse_status_file(jira, path, [issue])

    assert jira.calls == [
        ("add_comment", "LAA-1", text_to_adf("first line\nsecond line"))
    ]


def test_parse_status_file_fetches_issue_not_in_query(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    jira = FakeJira(issues=[FakeIssue("LAA-2")])

    jipdate.parse_status_file(jira, status_file("[LAA-2]\nhello\n"), None)

    assert jira.calls == [("add_comment", "LAA-2", text_to_adf("hello"))]


def test_parse_status_file_accepts_issue_tag_with_header(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    jira = FakeJira()

    jipdate.parse_status_file(
        jira, status_file("[LAA-1 | Fix it]\nhello\n"), [FakeIssue("LAA-1")]
    )

    assert jira.calls == [("add_comment", "LAA-1", text_to_adf("hello"))]


def test_parse_status_file_stop_tag_skips_until_next_issue(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    jira = FakeJira()
    path = status_file("[LAA-1]\none\n[STOP]\nhidden\n[LAA-2]\ntwo\n")

    jipdate.parse_status_file(jira, path, [FakeIssue("LAA-1"), FakeIssue("LAA-2")])

    assert jira.calls == [
        ("add_comment", "LAA-1", text_to_adf("one")),
        ("add_comment", "LAA-2", text_to_adf("two")),
    ]


def test_parse_status_file_fin_tag_stops_parsing(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    jira = FakeJira()
    path = status_file("[LAA-1]\none\n[FIN]\n[LAA-2]\ntwo\n")

    jipdate.parse_status_file(jira, path, [FakeIssue("LAA-1"), FakeIssue("LAA-2")])

    assert jira.calls == [("add_comment", "LAA-1", text_to_adf("one"))]


def test_parse_status_file_transition(set_args, set_config, answer, status_file):
    set_args("-s")
    jira = FakeJira()

    jipdate.parse_status_file(
        jira, status_file("[LAA-1]\nStatus: resolved\n"), [FakeIssue("LAA-1")]
    )

    assert jira.calls == [("transition_issue", "LAA-1", "21", {})]


def test_parse_status_file_transition_with_resolution(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    jira = FakeJira()
    path = status_file("[LAA-1]\nStatus: Resolved / won't do\n")

    jipdate.parse_status_file(jira, path, [FakeIssue("LAA-1")])

    assert jira.calls == [
        ("transition_issue", "LAA-1", "21", {"fields": {"resolution": {"id": "2"}}})
    ]


def test_parse_status_file_same_status_is_no_transition(
    set_args, set_config, answer, status_file, capsys
):
    set_args("-s")
    jira = FakeJira()
    path = status_file("[LAA-1]\nStatus: In Progress\n")

    with pytest.raises(SystemExit):
        jipdate.parse_status_file(jira, path, [FakeIssue("LAA-1")])

    assert jira.calls == []
    assert "No change, Jira was not updated!" in capsys.readouterr().out


def test_parse_status_file_invalid_transition_exits(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    path = status_file("[LAA-1]\nStatus: Flying\n")

    with pytest.raises(SystemExit) as e:
        jipdate.parse_status_file(FakeJira(), path, [FakeIssue("LAA-1")])

    assert e.value.code == 1


def test_parse_status_file_invalid_resolution_exits(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    path = status_file("[LAA-1]\nStatus: Resolved / Maybe\n")

    with pytest.raises(SystemExit) as e:
        jipdate.parse_status_file(FakeJira(), path, [FakeIssue("LAA-1")])

    assert e.value.code == 1


def test_parse_status_file_time_spent(set_args, set_config, answer, status_file):
    set_args("-s")
    jira = FakeJira()
    path = status_file("[LAA-1]\nworked\nTime spent: 3h\n")

    jipdate.parse_status_file(jira, path, [FakeIssue("LAA-1")])

    assert ("add_worklog", "LAA-1", "3h", text_to_adf("worked")) in jira.calls


def test_parse_status_file_skips_comment_lines(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    jira = FakeJira()
    path = status_file("[LAA-1]\n# not this\nthis\n")

    jipdate.parse_status_file(jira, path, [FakeIssue("LAA-1")])

    assert jira.calls == [("add_comment", "LAA-1", text_to_adf("this"))]


def test_parse_status_file_dry_run_does_not_update(
    set_args, set_config, answer, status_file, capsys
):
    set_args("-s", "--dry-run")
    jira = FakeJira()

    with pytest.raises(SystemExit):
        jipdate.parse_status_file(
            jira, status_file("[LAA-1]\nhello\n"), [FakeIssue("LAA-1")]
        )

    assert jira.calls == []
    assert "Comments will not be written to Jira!" in capsys.readouterr().out


def test_parse_status_file_answer_no_does_not_update(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    answer("n")
    jira = FakeJira()

    with pytest.raises(SystemExit):
        jipdate.parse_status_file(
            jira, status_file("[LAA-1]\nhello\n"), [FakeIssue("LAA-1")]
        )

    assert jira.calls == []


def test_parse_status_file_status_before_first_issue_is_ignored(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    jira = FakeJira()
    path = status_file("Status: Resolved\nTime spent: 1h\n[LAA-1]\nhello\n")

    jipdate.parse_status_file(jira, path, [FakeIssue("LAA-1")])

    assert jira.calls == [("add_comment", "LAA-1", text_to_adf("hello"))]


def test_parse_status_file_time_spent_after_stop_is_ignored(
    set_args, set_config, answer, status_file
):
    set_args("-s")
    jira = FakeJira()
    path = status_file("[LAA-1]\nhello\n[STOP]\nTime spent: 2h\n")

    jipdate.parse_status_file(jira, path, [FakeIssue("LAA-1")])

    assert jira.calls == [("add_comment", "LAA-1", text_to_adf("hello"))]


@pytest.mark.parametrize(
    "error",
    [
        JIRAError(status_code=404, text="Issue Does Not Exist"),
        JIRAError(status_code=403, text="You do not have permission"),
    ],
)
def test_parse_status_file_skips_issue_jira_can_not_fetch(
    set_args, set_config, answer, status_file, capsys, error
):
    set_args("-s")
    jira = FakeJira(issues=[FakeIssue("LAA-1")], issue_error=error)
    path = status_file("[LAA-1]\nfirst\n[LAA-2]\nsecond\n")

    jipdate.parse_status_file(jira, path, None)

    assert jira.calls == [("add_comment", "LAA-1", text_to_adf("first"))]
    assert f"[LAA-2] :  {error.text}" in capsys.readouterr().out


def test_print_status(capsys):
    jipdate.print_status(["[LAA-1]\n", "hello\n"])

    assert capsys.readouterr().out == "This is your status:\n\n---\n\n[LAA-1]\nhello\n"


def test_print_status_file(tmp_path, capsys):
    path = tmp_path / "status.txt"
    path.write_text("[LAA-1]\nhello\n")

    jipdate.print_status_file(str(path))

    assert capsys.readouterr().out == "[LAA-1]\nhello\n\n"


@pytest.fixture
def editor_env(monkeypatch):
    called = []
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr(jipdate, "call", called.append)

    def editor_env(existing_paths=()):
        monkeypatch.setattr(jipdate.os.path, "exists", existing_paths.__contains__)
        return called

    return editor_env


def test_open_editor_uses_visual(editor_env, monkeypatch):
    called = editor_env()
    monkeypatch.setenv("VISUAL", "emacs")

    jipdate.open_editor("status.txt")

    assert called == [["emacs", "status.txt"]]


@pytest.mark.parametrize(
    "existing, editor",
    [
        (["/usr/bin/editor", "/usr/bin/vim"], "/usr/bin/editor"),
        (["/usr/bin/vim", "/usr/bin/vi"], "/usr/bin/vim"),
        (["/usr/bin/vi"], "/usr/bin/vi"),
    ],
)
def test_open_editor_fallbacks(editor_env, existing, editor):
    called = editor_env(existing)

    jipdate.open_editor("status.txt")

    assert called == [[editor, "status.txt"]]


def test_open_editor_without_editor_exits(editor_env):
    called = editor_env()

    with pytest.raises(SystemExit) as e:
        jipdate.open_editor("status.txt")

    assert e.value.code == os.EX_CONFIG
    assert called == []


def test_write_last_jira_comment_ignores_encode_error():
    class AsciiFile:
        def write(self, text):
            raise UnicodeEncodeError("ascii", text, 0, 1, "not ascii")

    jira = FakeJira(comments=[fake_comment("h\u00e4j")])

    jipdate.write_last_jira_comment(AsciiFile(), jira, "LAA-1")


def test_should_update_asks_again_on_bad_answer(set_config, monkeypatch, capsys):
    replies = iter(["maybe", " Y "])
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))

    assert jipdate.should_update() == "y"

    out = capsys.readouterr().out
    assert "Incorrect input: maybe" in out
    assert out.count("Server to update: https://projects.linaro.org") == 2


def test_parse_status_file_prints_status_without_s(
    set_args, set_config, answer, status_file, capsys
):
    set_args()
    jira = FakeJira()

    jipdate.parse_status_file(
        jira, status_file("[LAA-1]\nhello\n"), [FakeIssue("LAA-1")]
    )

    out = capsys.readouterr().out
    assert "Successfully updated your Jira tickets!" in out
    assert out.endswith("This is your status:\n\n---\n\n[LAA-1]\nhello\n")


def test_parse_status_file_no_change_prints_status_without_s(
    set_args, set_config, answer, status_file, capsys
):
    set_args()

    with pytest.raises(SystemExit):
        jipdate.parse_status_file(
            FakeJira(), status_file("[LAA-1]\n"), [FakeIssue("LAA-1")]
        )

    assert capsys.readouterr().out.endswith("---\n\n[LAA-1]\n")


@pytest.mark.parametrize(
    "verbose, level", [(False, logging.ERROR), (True, logging.DEBUG)]
)
def test_initialize_logger(monkeypatch, verbose, level):
    called = {}
    monkeypatch.setattr(jipdate.log, "basicConfig", lambda **kw: called.update(kw))

    jipdate.initialize_logger(SimpleNamespace(verbose=verbose))

    assert called["level"] == level


@pytest.fixture
def run_main(monkeypatch, set_config, answer):
    editor = []
    logins = []
    monkeypatch.setattr(cfg, "initiate_config", lambda: None)
    monkeypatch.setattr(jipdate, "open_editor", editor.append)

    def run_main(jira, *argv):
        monkeypatch.setattr(sys, "argv", ["jipdate", *argv])

        def login(use_test_server):
            logins.append(use_test_server)
            return jira, "first.last@linaro.org"

        monkeypatch.setattr(jiralogin, "get_jira_instance", login)
        jipdate.main()
        return editor, logins

    run_main.logins = logins
    return run_main


@pytest.mark.parametrize(
    "argv",
    [[], ["-f", "status.txt", "-x"], ["-f", "status.txt", "-e"], ["-f", "s", "-p"]],
)
def test_main_bad_arguments_exit(run_main, argv):
    with pytest.raises(SystemExit) as e:
        run_main(FakeJira(), *argv)

    assert e.value.code == os.EX_USAGE
    assert run_main.logins == []


def test_main_query_and_print(run_main, tmp_path, capsys):
    path = tmp_path / "status.txt"
    jira = FakeJira(issues=[FakeIssue("LAA-1", summary="Fix it")])

    with pytest.raises(SystemExit) as e:
        run_main(jira, "-q", "-p", "-f", str(path))

    assert e.value.code == os.EX_OK
    out = capsys.readouterr().out
    assert out.startswith("Subject: [Weekly] Week ending ")
    assert "[LAA-1]\n# Header: Fix it\n" in out


def test_main_query_edit_and_update(run_main, tmp_path, monkeypatch):
    path = tmp_path / "status.txt"
    issue = FakeIssue("LAA-1")
    jira = FakeJira(issues=[issue])

    def write_update(filename):
        with open(filename, "a") as f:
            f.write("done\n")

    monkeypatch.setattr(jipdate, "open_editor", write_update)
    run_main(jira, "-q", "-s", "-t", "-f", str(path))

    assert ("add_comment", "LAA-1", text_to_adf("done")) in jira.calls


def test_main_file_opens_editor_and_updates(run_main, status_file):
    path = status_file("[LAA-2]\nhello\n")
    jira = FakeJira(issues=[FakeIssue("LAA-2")])

    editor, logins = run_main(jira, "-s", "-f", path)

    assert editor == [path]
    assert logins == [False]
    assert jira.calls == [("add_comment", "LAA-2", text_to_adf("hello"))]


def test_main_file_without_editor(run_main, set_config, status_file):
    set_config({"text-editor": False})
    path = status_file("[LAA-2]\nhello\n")
    jira = FakeJira(issues=[FakeIssue("LAA-2")])

    editor, _ = run_main(jira, "-s", "-f", path)

    assert editor == []
    assert jira.calls == [("add_comment", "LAA-2", text_to_adf("hello"))]


def test_module_runs_main(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["jipdate", "--version"])
    monkeypatch.delitem(sys.modules, "jipdate.jipdate")

    with pytest.raises(SystemExit) as e:
        runpy.run_module("jipdate.jipdate", run_name="__main__")

    assert e.value.code == 0
    assert capsys.readouterr().out.startswith("jipdate, ")
