import pytest

from ledgerlab.workbook import CellError, Workbook, references


def wb_with(**cells):
    wb = Workbook()
    wb.add_sheet("S")
    wb.add_sheet("T")
    for a, c in cells.items():
        wb.set("S", a, c)
    return wb


@pytest.mark.parametrize("formula,expected", [
    ("=1+2*3", 7), ("=(1+2)*3", 9), ("=2^3^2", 512), ("=-2^2", 4), ("=10/4", 2.5),
    ("=SUM(A1:A3)", 6), ("=AVERAGE(A1:A3)", 2), ("=MAX(A1:A3,10)", 10), ("=MIN(A1:A3)", 1),
    ("=ROUND(2.567,1)", 2.6), ("=ABS(-3)", 3), ("=$A$1+A2", 3), ("=T!B2*2", 14),
])
def test_eval(formula, expected):
    wb = wb_with(A1=1, A2=2, A3=3, B1=formula)
    wb.set("T", "B2", 7)
    assert wb.value("S", "B1") == pytest.approx(expected)


def test_errors():
    wb = wb_with(A1="=1/0", A2="=A3", A3="=A2", A4="=A1+1", A5="=SUM(", A6="=FOO(1)", A7="text", A8="=A7*2")
    assert wb.value("S", "A1") == "#DIV/0!"
    assert isinstance(wb.value("S", "A2"), CellError)
    assert wb.value("S", "A4") == "#DIV/0!"
    assert wb.value("S", "A5") == "#PARSE!"
    assert wb.value("S", "A6") == "#NAME?"
    assert wb.value("S", "A8") == "#VALUE!"


def test_empty_is_zero_and_cache_invalidates():
    wb = wb_with(A1="=B9+1")
    assert wb.value("S", "A1") == 1
    wb.set("S", "B9", 4)
    assert wb.value("S", "A1") == 5


def test_references():
    assert references("=SUM(A1:A2)+T!$C$3", "S") == [("S", "A1"), ("S", "A2"), ("T", "C3")]


def test_xlsx_roundtrip(tmp_path):
    wb = wb_with(A1=2, A2="=A1*3")
    p = tmp_path / "x.xlsx"
    wb.to_xlsx(str(p))
    back = Workbook.from_xlsx(str(p))
    assert back.value("S", "A2") == 6
