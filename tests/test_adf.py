import pytest

from jipdate.adf import adf_to_text, text_to_adf


def doc(*content):
    return {"type": "doc", "version": 1, "content": list(content)}


def paragraph(*content):
    return {"type": "paragraph", "content": list(content)}


def text(value):
    return {"type": "text", "text": value}


def test_adf_to_text_returns_plain_string_unchanged():
    assert adf_to_text("first\nsecond") == "first\nsecond"


def test_adf_to_text_joins_inline_nodes_in_a_paragraph():
    node = doc(paragraph(text("Hello "), text("world")))
    assert adf_to_text(node) == "Hello world"


def test_adf_to_text_puts_each_block_on_its_own_line():
    node = doc(paragraph(text("one")), paragraph(text("two")))
    assert adf_to_text(node) == "one\ntwo"


def test_adf_to_text_keeps_empty_paragraph_as_empty_line():
    node = doc(paragraph(text("a")), {"type": "paragraph"}, paragraph(text("b")))
    assert adf_to_text(node) == "a\n\nb"


def test_adf_to_text_turns_hard_break_into_newline():
    node = doc(paragraph(text("a"), {"type": "hardBreak"}, text("b")))
    assert adf_to_text(node) == "a\nb"


def test_adf_to_text_reads_nested_lists():
    node = doc(
        {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [paragraph(text("first"))]},
                {"type": "listItem", "content": [paragraph(text("second"))]},
            ],
        }
    )
    assert adf_to_text(node) == "first\nsecond"


def test_adf_to_text_reads_heading_and_code_block():
    node = doc(
        {"type": "heading", "attrs": {"level": 1}, "content": [text("Title")]},
        {"type": "codeBlock", "content": [text("x = 1")]},
    )
    assert adf_to_text(node) == "Title\nx = 1"


def test_adf_to_text_uses_mention_text():
    mention = {"type": "mention", "attrs": {"id": "123", "text": "@Anders"}}
    node = doc(paragraph(mention, text(" please check")))
    assert adf_to_text(node) == "@Anders please check"


def test_adf_to_text_uses_inline_card_url():
    card = {"type": "inlineCard", "attrs": {"url": "https://example.org/x"}}
    node = doc(paragraph(text("see "), card))
    assert adf_to_text(node) == "see https://example.org/x"


def test_adf_to_text_ignores_text_marks():
    bold = {"type": "text", "text": "bold", "marks": [{"type": "strong"}]}
    assert adf_to_text(doc(paragraph(bold))) == "bold"


def test_text_to_adf_makes_one_paragraph_per_line():
    assert text_to_adf("one\ntwo") == doc(
        paragraph(text("one")), paragraph(text("two"))
    )


def test_text_to_adf_has_no_empty_text_nodes():
    assert text_to_adf("a\n\nb")["content"][1] == {"type": "paragraph"}


@pytest.mark.parametrize(
    "value",
    ["", "one line", "a\nb", "a\n\nb", "trailing newline\n", "\nleading newline"],
)
def test_text_survives_round_trip(value):
    assert adf_to_text(text_to_adf(value)) == value
