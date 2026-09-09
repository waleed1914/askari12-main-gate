from askari_vms.ui.pagination import DEFAULT_PAGE_SIZE, PAGE_SIZES, Pager, page_numbers


def test_page_numbers_keep_the_ends_and_a_window_around_the_current_page() -> None:
    assert page_numbers(1, 1) == [1]
    assert page_numbers(1, 5) == [1, 2, 3, 4, 5]  # never hide a single page
    assert page_numbers(3, 5) == [1, 2, 3, 4, 5]
    assert page_numbers(20, 40) == [1, "…", 18, 19, 20, 21, 22, "…", 40]
    assert page_numbers(1, 40) == [1, 2, 3, "…", 40]
    assert page_numbers(40, 40) == [1, "…", 38, 39, 40]


def test_page_numbers_never_go_out_of_range() -> None:
    for total in (1, 2, 7, 40):
        for current in range(1, total + 1):
            pages = [p for p in page_numbers(current, total) if isinstance(p, int)]
            assert pages == sorted(pages)
            assert all(1 <= p <= total for p in pages)
            assert current in pages and 1 in pages and total in pages


def test_pager_slices_the_current_page(qapp) -> None:
    rows = list(range(95))
    pager = Pager(page_size=20)
    assert pager.page_size == DEFAULT_PAGE_SIZE

    assert pager.slice(rows) == list(range(0, 20))
    assert pager.page_count == 5
    assert "Showing 1–20 of 95" in pager.summary.text()
    assert not pager.prev_button.isEnabled() and pager.next_button.isEnabled()

    pager.set_page(5)
    assert pager.slice(rows) == list(range(80, 95)), "the final page is short"
    assert "Showing 81–95 of 95" in pager.summary.text()
    assert pager.prev_button.isEnabled() and not pager.next_button.isEnabled()


def test_pager_clamps_out_of_range_pages(qapp) -> None:
    pager = Pager(page_size=10)
    pager.slice(list(range(35)))
    pager.set_page(99)
    assert pager.page == 4
    pager.set_page(-3)
    assert pager.page == 1


def test_pager_returns_to_page_one_when_the_size_changes(qapp) -> None:
    pager = Pager(page_size=10)
    pager.slice(list(range(100)))
    pager.set_page(7)
    assert pager.page == 7
    pager.size_box.setCurrentText("50")
    assert pager.page == 1
    assert pager.slice(list(range(100))) == list(range(0, 50))


def test_pager_handles_a_list_that_shrinks_under_it(qapp) -> None:
    pager = Pager(page_size=10)
    pager.slice(list(range(100)))
    pager.set_page(9)
    # A filter now matches far fewer rows; the pager must land on a real page.
    visible = pager.slice(list(range(12)))
    assert pager.page == 2
    assert visible == [10, 11]


def test_pager_copes_with_an_empty_list(qapp) -> None:
    pager = Pager()
    assert pager.slice([]) == []
    assert pager.page_count == 1
    assert pager.summary.text() == "No records"
    assert not pager.prev_button.isEnabled() and not pager.next_button.isEnabled()


def test_offered_page_sizes_match_the_reference(qapp) -> None:
    assert PAGE_SIZES == (10, 20, 50, 100)
    pager = Pager()
    assert [pager.size_box.itemText(i) for i in range(pager.size_box.count())] == ["10", "20", "50", "100"]
