from parser import parse
def test_smoke():
    assert parse('x=1') == {'x': 1}
