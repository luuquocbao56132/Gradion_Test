from pathlib import Path

from app import files

PNG = b"\x89PNG\r\n\x1a\n-fake-bytes"


def test_book_is_written_and_read_back_verbatim(tmp_path):
    rel = files.write_book(tmp_path, "p1", "Once upon a time.\nChapter 1.")
    assert rel == "projects/p1/book.txt"
    assert (tmp_path / rel).read_text(encoding="utf-8") == "Once upon a time.\nChapter 1."
    assert files.read_book(tmp_path, "p1") == "Once upon a time.\nChapter 1."


def test_stored_paths_are_relative_so_data_dir_stays_relocatable(tmp_path):
    rel = files.save_portrait_bytes(tmp_path, "p1", "c1", PNG)
    assert rel == "projects/p1/portraits/c1.png"
    assert not Path(rel).is_absolute()
    assert (tmp_path / rel).read_bytes() == PNG


def test_illustration_path_derives_from_the_chapter_id(tmp_path):
    rel = files.save_illustration_bytes(tmp_path, "p1", "ch1", PNG)
    assert rel == "projects/p1/illustrations/ch1.png"
    assert (tmp_path / rel).read_bytes() == PNG


def test_a_rewrite_overwrites_its_own_orphan_leaving_no_tmp_file(tmp_path):
    files.save_portrait_bytes(tmp_path, "p1", "c1", b"first")
    files.save_portrait_bytes(tmp_path, "p1", "c1", b"second")
    portraits = tmp_path / "projects" / "p1" / "portraits"
    assert (portraits / "c1.png").read_bytes() == b"second"
    assert list(portraits.iterdir()) == [portraits / "c1.png"]


def test_excerpt_collapses_whitespace_and_ellipsises(tmp_path):
    assert files.excerpt("a   b\n\nc") == "a b c"
    long = "word " * 200
    out = files.excerpt(long)
    assert len(out) == files.EXCERPT_CHARS + 1 and out.endswith("…")


def test_absolute_resolves_a_stored_relative_path(tmp_path):
    rel = files.save_portrait_bytes(tmp_path, "p1", "c1", PNG)
    assert files.absolute(tmp_path, rel) == (tmp_path / rel).resolve()
