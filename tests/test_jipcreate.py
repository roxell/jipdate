import logging
import os
import sys
from types import SimpleNamespace

import pytest

from jipdate import cfg, jipcreate, jiralogin
from jipdate.adf import text_to_adf

SERVER = "https://projects.linaro.org"


def create_meta(required=(), missing=()):
    fields = {
        name: {"required": name in required, "hasDefaultValue": False}
        for name in jipcreate.jira_field_to_yaml
        if name not in missing
    }
    for name in required:
        fields.setdefault(name, {"required": True, "hasDefaultValue": False})
    fields["customfield_10104"]["allowedValues"] = [
        {"self": "https://x/option/1", "value": "Arm", "id": 1},
        {"self": "https://x/option/2", "value": "Qualcomm", "id": 2},
    ]
    return {"projects": [{"issuetypes": [{"fields": fields}]}]}


class FakeResultList(list):
    def __init__(self, items, is_last):
        super().__init__(items)
        self.isLast = is_last


class FakeExistingIssue:
    def __init__(self, key, calls):
        self.key = key
        self.calls = calls

    def update(self, fields):
        self.calls.append(("update", self.key, fields))

    def __str__(self):
        return self.key


class FakeJira:
    def __init__(self, meta=None, users=None, sprint_pages=None, boards=None):
        self.meta = meta if meta is not None else create_meta()
        self.users = users or {}
        self.sprint_pages = sprint_pages or {}
        self._boards = boards or []
        self.calls = []
        self.created = 0

    def createmeta(self, projectKeys, issuetypeNames, expand):
        return self.meta

    def search_assignable_users_for_issues(self, query, project):
        return [SimpleNamespace(accountId=i) for i in self.users.get(query, [])]

    def project_components(self, project):
        return [
            SimpleNamespace(name="kernel", id=10),
            SimpleNamespace(name="tools", id=20),
        ]

    def boards(self, projectKeyOrID):
        return self._boards

    def sprints(self, board_id, maxResults, startAt):
        self.calls.append(("sprints", board_id, startAt))
        return self.sprint_pages[board_id][startAt // maxResults]

    def issue(self, key):
        return FakeExistingIssue(key, self.calls)

    def create_issue(self, fields):
        self.created += 1
        key = f"LAA-{100 + self.created}"
        self.calls.append(("create_issue", key, fields))
        return key


def sprint(name, sprint_id):
    return SimpleNamespace(name=name, id=sprint_id)


@pytest.fixture
def run_main(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cfg, "initiate_config", lambda: None)
    monkeypatch.setattr(cfg, "yml_config", {})

    def run_main(jira, yml, *argv):
        path = tmp_path / "issues.yml"
        path.write_text(yml)
        monkeypatch.setattr(sys, "argv", ["jipcreate", "-f", str(path), *argv])
        monkeypatch.setattr(jiralogin, "get_jira_instance", lambda test: (jira, "me"))
        jipcreate.main()
        return capsys.readouterr().out

    return run_main


TASK = "- Project: LAA\n  IssueType: Task\n  Summary: Fix it\n"


def created_fields(jira, index=0):
    return [call for call in jira.calls if call[0] == "create_issue"][index][2]


def test_parse_issue_file_reads_yaml_list(tmp_path):
    path = tmp_path / "issues.yml"
    path.write_text(TASK + "- Project: LAA\n  IssueType: Bug\n")

    issues = jipcreate.parse_issue_file(str(path))

    assert issues == [
        {"Project": "LAA", "IssueType": "Task", "Summary": "Fix it"},
        {"Project": "LAA", "IssueType": "Bug"},
    ]


def test_parse_issue_file_missing_file(tmp_path, caplog):
    path = str(tmp_path / "nope.yml")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as e:
        jipcreate.parse_issue_file(path)

    assert e.value.code == -1
    assert f"Issue file not found: {path}" in caplog.text


def test_main_without_file_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["jipcreate"])
    monkeypatch.setattr(cfg, "initiate_config", lambda: None)

    with pytest.raises(SystemExit) as e:
        jipcreate.main()

    assert e.value.code == os.EX_USAGE


def test_main_creates_issue(run_main):
    jira = FakeJira()

    out = run_main(jira, TASK)

    assert created_fields(jira) == {
        "project": {"key": "LAA"},
        "issuetype": {"name": "Task"},
        "summary": "Fix it",
    }
    assert out == f"New issue created: {SERVER}/browse/LAA-101\n"


def test_main_updates_issue_with_key(run_main):
    jira = FakeJira()

    out = run_main(jira, TASK + "  Key: LAA-7\n")

    assert jira.calls == [
        (
            "update",
            "LAA-7",
            {
                "project": {"key": "LAA"},
                "issuetype": {"name": "Task"},
                "summary": "Fix it",
            },
        )
    ]
    assert out == f"Existing issue updated: {SERVER}/browse/LAA-7\n"


def test_main_sets_optional_fields(run_main):
    jira = FakeJira(users={"anders@linaro.org": ["acc-1"], "bob": ["acc-2"]})
    yml = TASK + (
        "  Description: |\n    line 1\n    line 2\n"
        "  OriginalEstimate: 2d\n"
        "  AssigneeEmail: anders@linaro.org\n"
        "  ClientStakeholder: Qualcomm\n"
        "  Components: kernel, tools, unknown\n"
        "  Due date: 2026-12-24\n"
        "  Share Visibility: [anders@linaro.org, bob]\n"
    )

    run_main(jira, yml)

    fields = created_fields(jira)
    assert fields["description"] == text_to_adf("line 1\nline 2\n")
    assert fields["timetracking"] == {"originalEstimate": "2d"}
    assert fields["assignee"] == {"id": "acc-1"}
    assert fields["customfield_10104"] == [
        {"self": "https://x/option/2", "value": "Qualcomm", "id": "2"}
    ]
    assert fields["components"] == [{"id": "10"}, {"id": "20"}]
    assert fields["duedate"] == "2026-12-24"
    assert fields["customfield_10034"] == [{"id": "acc-1"}, {"id": "acc-2"}]


def test_main_skips_unknown_users(run_main):
    jira = FakeJira(users={"bob": ["acc-2"]})
    yml = TASK + "  AssigneeEmail: nobody\n  Share Visibility: [nobody, bob]\n"

    out = run_main(jira, yml)

    fields = created_fields(jira)
    assert "assignee" not in fields
    assert fields["customfield_10034"] == [{"id": "acc-2"}]
    assert "Share Visibility: user nobody not found\n" in out


def test_main_epic_name_defaults_to_summary(run_main):
    jira = FakeJira()
    yml = (
        "- Project: LAA\n  IssueType: Epic\n  Summary: Big thing\n"
        "- Project: LAA\n  IssueType: Epic\n  Summary: Other\n"
        "  Epic Name: Short\n"
    )

    run_main(jira, yml)

    assert created_fields(jira, 0)["customfield_10011"] == "Big thing"
    assert created_fields(jira, 1)["customfield_10011"] == "Short"


def test_main_epic_link_to_issue_created_in_same_file(run_main):
    jira = FakeJira()
    yml = (
        "- Project: LAA\n  IssueType: Epic\n  Summary: Big thing\n"
        "- Project: LAA\n  IssueType: Task\n  Summary: Part\n"
        "  EpicLink: Big thing\n"
        "- Project: LAA\n  IssueType: Task\n  Summary: Other\n"
        "  EpicLink: LAA-5\n"
    )

    run_main(jira, yml)

    assert created_fields(jira, 1)["customfield_10014"] == "LAA-101"
    assert created_fields(jira, 2)["customfield_10014"] == "LAA-5"


def test_main_sprint_from_scrum_board(run_main):
    jira = FakeJira(
        boards=[
            SimpleNamespace(id=1, type="kanban"),
            SimpleNamespace(id=2, type="scrum"),
        ],
        sprint_pages={
            2: [
                FakeResultList([sprint("Sprint 1", 501)], is_last=False),
                FakeResultList([sprint("Sprint 2", 502)], is_last=True),
            ]
        },
    )

    run_main(jira, TASK + "  Sprint: Sprint 2\n")

    assert created_fields(jira)["customfield_10020"] == 502
    assert ("sprints", 1, 0) not in jira.calls


def test_main_sprint_not_found_skips_issue(run_main):
    jira = FakeJira(
        boards=[SimpleNamespace(id=2, type="scrum")],
        sprint_pages={2: [FakeResultList([sprint("Sprint 1", 501)], is_last=True)]},
    )

    out = run_main(jira, TASK + "  Sprint: Sprint 9\n")

    assert jira.created == 0
    assert out == 'Sprint "Sprint 9" not found in project LAA\n'


def test_get_sprints_reads_all_pages():
    jira = FakeJira(
        sprint_pages={
            3: [
                FakeResultList([sprint("a", 1)], is_last=False),
                FakeResultList([sprint("b", 2)], is_last=True),
            ]
        }
    )

    sprints = jipcreate.get_sprints(jira, 3)

    assert [s.name for s in sprints] == ["a", "b"]
    assert jira.calls == [("sprints", 3, 0), ("sprints", 3, 50)]


def test_main_dry_run_prints_fields(run_main):
    jira = FakeJira()

    out = run_main(jira, TASK, "--dry-run")

    assert jira.created == 0
    assert out == (
        "This issue would have been created when running without '--dry-run':\n"
        "project: {'key': 'LAA'}\n"
        "issuetype: {'name': 'Task'}\n"
        "summary: Fix it\n"
    )


def test_main_missing_project_or_issue_type(run_main):
    jira = FakeJira()

    out = run_main(jira, "- Summary: Fix it\n")

    assert jira.created == 0
    assert out == "Please specify 'Project' and 'IssueType'.\n"


def test_main_field_not_allowed_exits(run_main):
    jira = FakeJira(meta=create_meta(missing=("duedate",)))

    with pytest.raises(SystemExit) as e:
        run_main(jira, TASK + "  Due date: 2026-12-24\n")

    assert e.value.code == os.EX_USAGE
    assert jira.created == 0


@pytest.mark.parametrize(
    "field, name",
    [("customfield_10014", "EpicLink"), ("customfield_99999", "customfield_99999")],
)
def test_main_required_field_missing_exits(run_main, capsys, field, name):
    jira = FakeJira(meta=create_meta(required=(field,)))

    with pytest.raises(SystemExit) as e:
        run_main(jira, TASK)

    assert e.value.code == os.EX_USAGE
    assert capsys.readouterr().out == f"Field {name} required but not set.\n"


def test_main_epic_without_summary_reports_required_field(run_main, capsys):
    jira = FakeJira(meta=create_meta(required=("summary",)))

    with pytest.raises(SystemExit) as e:
        run_main(jira, "- Project: LAA\n  IssueType: Epic\n")

    assert e.value.code == os.EX_USAGE
    assert capsys.readouterr().out == "Field Summary required but not set.\n"


def test_main_dry_run_without_summary(run_main):
    out = run_main(FakeJira(), "- Project: LAA\n  IssueType: Task\n", "--dry-run")

    assert out == (
        "This issue would have been created when running without '--dry-run':\n"
        "project: {'key': 'LAA'}\n"
        "issuetype: {'name': 'Task'}\n"
    )


def test_main_create_without_summary(run_main):
    jira = FakeJira()

    out = run_main(jira, "- Project: LAA\n  IssueType: Task\n")

    assert out == f"New issue created: {SERVER}/browse/LAA-101\n"
