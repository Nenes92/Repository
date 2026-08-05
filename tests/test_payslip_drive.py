from payslip_drive import pending_drive_files, safe_pdf_filename, unique_pdf_filename


def test_safe_pdf_filename_removes_path_and_adds_extension():
    assert safe_pdf_filename("../../Cedolino luglio") == "Cedolino luglio.pdf"
    assert safe_pdf_filename("cedolino.pdf") == "cedolino.pdf"


def test_unique_pdf_filename_never_overwrites_name_collision():
    assert unique_pdf_filename(
        "cedolino.pdf",
        ["CEDOLINO.PDF", "cedolino-20260804.pdf"],
        "20260804",
    ) == "cedolino-20260804-2.pdf"


def test_pending_drive_files_excludes_confirmed_and_sorts_recent_first():
    files = [
        {"id": "old", "name": "old.pdf", "modifiedTime": "2026-06-01T10:00:00Z"},
        {"id": "done", "name": "done.pdf", "modifiedTime": "2026-08-01T10:00:00Z"},
        {"id": "new", "name": "new.pdf", "modifiedTime": "2026-07-01T10:00:00Z"},
    ]
    registry = {"done": {"status": "Confermato"}}
    assert [item["id"] for item in pending_drive_files(files, registry)] == ["new", "old"]

