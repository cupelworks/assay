def sentence(text: str) -> str:
    """text with its first letter capitalized.

    Every message stored for the UI to show as-is — a run's error, a test
    type's detail, a check's error, a settings validation message — starts
    like a sentence, including text produced by a library (a regex
    compiler's error, a driver's exception) that starts in lower case.
    """
    return text[:1].upper() + text[1:]
