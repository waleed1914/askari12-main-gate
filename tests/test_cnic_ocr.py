from askari_vms.cnic_ocr import CNICCapture, OCRField, parse_identity_text


def test_parses_labelled_english_cnic_fields() -> None:
    fields = parse_identity_text([
        "Name", "SANA MALIK", "Father Name: IMRAN MALIK",
        "Identity Number 35202-7654321-9", "Date of Birth 01.02.1990",
        "Date of Issue 03.04.2015", "Date of Expiry 03.04.2025",
    ], [0.98, 0.91, 0.89, 0.96, 0.87, 0.86, 0.85])
    assert fields["visitor_name"].value == "SANA MALIK"
    assert fields["father_name"].value == "IMRAN MALIK"
    assert fields["cnic"].value == "35202-7654321-9"
    assert fields["date_of_birth"].value == "01.02.1990"
    assert fields["cnic_issue_date"].value == "03.04.2015"
    assert fields["cnic_expiry_date"].value == "03.04.2025"


def test_name_is_not_confused_with_father_name() -> None:
    fields = parse_identity_text(["Father Name", "JAN MUHAMMAD"], [0.95, 0.94])
    assert "visitor_name" not in fields
    assert fields["father_name"].value == "JAN MUHAMMAD"


def test_dates_are_matched_by_position_not_ocr_reading_order() -> None:
    lines = ["Date of Birth", "11.11.2000", "Date of Expiry", "Date of Issue",
             "24.01.2029", "24.01.2019"]
    scores = [0.95] * len(lines)
    # DOB is right/top; expiry right/bottom; issue left/bottom. OCR deliberately
    # returns the two labels before either value, matching the real camera result.
    boxes = [
        [[900, 100], [1200, 100], [1200, 140], [900, 140]],
        [[920, 150], [1200, 150], [1200, 190], [920, 190]],
        [[900, 220], [1200, 220], [1200, 260], [900, 260]],
        [[450, 220], [750, 220], [750, 260], [450, 260]],
        [[920, 270], [1200, 270], [1200, 310], [920, 310]],
        [[470, 270], [750, 270], [750, 310], [470, 310]],
    ]
    fields = parse_identity_text(lines, scores, boxes)
    assert fields["date_of_birth"].value == "11.11.2000"
    assert fields["cnic_issue_date"].value == "24.01.2019"
    assert fields["cnic_expiry_date"].value == "24.01.2029"


def test_portal_accepts_an_injected_local_reader(qapp) -> None:
    from askari_vms.ui.pages.entry_portal import EntryPortalWindow

    class Reader:
        def capture_and_read(self):
            return CNICCapture("sample.jpg", {
                "visitor_name": OCRField("Sana Malik", 0.94),
                "cnic": OCRField("35202-7654321-9", 0.61),
            })

    portal = EntryPortalWindow(cnic_reader=Reader())
    assert portal.capture_cnic()
    assert portal.fields["visitor_name"].text() == "Sana Malik"
    assert portal.fields["cnic"].property("ocrConfidence") == "low"
    assert portal._captured["cnic"] is True


# The exact detector output from a real capture: the card prints Identity Number and
# Date of Birth side by side, so their values come back as one run.
MERGED_LINES = [
    "PAKISTAN", "National Identity Card", "ISLAMIC REPUBLIC OF PAKISTAN",
    "Name", "Shahzaib", "jol", "Father Name", "Jan Muhammad", "9n",
    "Gender  Country of Stay", "M", "PakistanoLo",
    "Identity Number", "Date of Birth", "31101-6646517-711.11.2000",
    "Date of Issue", "Date of Expiry", "24.01.2019", "24.01.2029",
]
MERGED_SCORES = [1.0, 1.0, 1.0, 1.0, 1.0, 0.86, 1.0, 1.0, 0.71,
                 0.98, 1.0, 0.89, 0.97, 1.0, 1.0, 0.96, 1.0, 1.0, 1.0]


def test_a_cnic_glued_to_a_date_yields_both() -> None:
    """Neither had a word boundary against the other, so both were being dropped."""
    fields = parse_identity_text(MERGED_LINES, MERGED_SCORES)
    assert fields["cnic"].value == "31101-6646517-7"
    assert fields["date_of_birth"].value == "11.11.2000"


def test_adjacent_date_labels_pair_with_their_values_in_order() -> None:
    """Both labels detect before either value; matching each to the next line filed
    the issue date under expiry."""
    fields = parse_identity_text(MERGED_LINES, MERGED_SCORES)
    assert fields["cnic_issue_date"].value == "24.01.2019"
    assert fields["cnic_expiry_date"].value == "24.01.2029"


def test_every_field_is_read_from_the_real_capture_without_geometry() -> None:
    fields = parse_identity_text(MERGED_LINES, MERGED_SCORES)
    assert {k: v.value for k, v in fields.items()} == {
        "cnic": "31101-6646517-7",
        "date_of_birth": "11.11.2000",
        "cnic_issue_date": "24.01.2019",
        "cnic_expiry_date": "24.01.2029",
        "visitor_name": "Shahzaib",
        "father_name": "Jan Muhammad",
    }


def test_geometry_resolves_the_merged_card_completely() -> None:
    boxes = [[[0, y], [100, y], [100, y + 20], [0, y + 20]] for y in range(0, 19 * 40, 40)]
    # Place the two value rows to match the printed card: issue left, expiry right.
    boxes[12] = [[40, 480], [200, 480], [200, 500], [40, 500]]    # Identity Number
    boxes[13] = [[420, 480], [560, 480], [560, 500], [420, 500]]  # Date of Birth
    boxes[14] = [[40, 530], [560, 530], [560, 550], [40, 550]]    # merged values
    boxes[15] = [[40, 600], [200, 600], [200, 620], [40, 620]]    # Date of Issue
    boxes[16] = [[420, 600], [560, 600], [560, 620], [420, 620]]  # Date of Expiry
    boxes[17] = [[40, 650], [200, 650], [200, 670], [40, 670]]    # 24.01.2019
    boxes[18] = [[420, 650], [560, 650], [560, 670], [420, 670]]  # 24.01.2029

    fields = parse_identity_text(MERGED_LINES, MERGED_SCORES, boxes)
    assert fields["cnic"].value == "31101-6646517-7"
    assert fields["date_of_birth"].value == "11.11.2000"
    assert fields["cnic_issue_date"].value == "24.01.2019"
    assert fields["cnic_expiry_date"].value == "24.01.2029"
    assert fields["visitor_name"].value == "Shahzaib"
    assert fields["father_name"].value == "Jan Muhammad"


def test_a_separated_cnic_still_parses_on_its_own_line() -> None:
    fields = parse_identity_text(["Identity Number", "35202-7654321-9"], [0.9, 0.95])
    assert fields["cnic"].value == "35202-7654321-9"
