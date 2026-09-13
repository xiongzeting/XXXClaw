from calculator import solve
def test_smoke():
    assert solve({'lines': [{'id': 'a', 'units': 1, 'exempt': False}]}) == {'lines': [{'id': 'a', 'base_cents': 100, 'tax_cents': 7, 'total_cents': 107}], 'grand_total_cents': 107}
