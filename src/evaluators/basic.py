def evaluate_output(output: dict[str, object]) -> list[str]:
    notes: list[str] = []
    if not output.get("draft"):
        notes.append("draft missing")
    if not output.get("review_notes"):
        notes.append("review notes missing")
    if not output.get("channels"):
        notes.append("channel drafts missing")
    return notes
