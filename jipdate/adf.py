INLINE_CONTAINERS = ("paragraph", "heading")


def adf_to_text(node):
    if isinstance(node, str):
        return node

    node_type = node.get("type")
    if node_type == "text":
        return node.get("text", "")
    if node_type == "hardBreak":
        return "\n"
    if node_type == "mention":
        return node.get("attrs", {}).get("text", "")
    if node_type == "inlineCard":
        return node.get("attrs", {}).get("url", "")

    children = [adf_to_text(child) for child in node.get("content", [])]
    if node_type in INLINE_CONTAINERS:
        return "".join(children)
    return "\n".join(children)


def text_to_adf(text):
    paragraphs = []
    for line in text.split("\n"):
        paragraph = {"type": "paragraph"}
        if line:
            paragraph["content"] = [{"type": "text", "text": line}]
        paragraphs.append(paragraph)
    return {"type": "doc", "version": 1, "content": paragraphs}
