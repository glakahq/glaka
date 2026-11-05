from glaka.chunking import split_into_chunks


def test_headings_start_a_new_chunk() -> None:
    text = "# Prices\n\nShower: £450\n\n## Tiling\n\n£55 per m2"

    assert split_into_chunks(text) == ["# Prices\n\nShower: £450", "## Tiling\n\n£55 per m2"]


def test_paragraphs_are_packed_up_to_max_chars() -> None:
    text = "aaaa\n\nbbbb\n\ncccc"

    assert split_into_chunks(text, max_chars=10) == ["aaaa\n\nbbbb", "cccc"]


def test_empty_text_gives_no_chunks() -> None:
    assert split_into_chunks("  \n\n ") == []
