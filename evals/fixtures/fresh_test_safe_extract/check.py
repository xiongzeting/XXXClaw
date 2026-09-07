from pathlib import Path
assert Path('reports.txt').read_text() == 'report-new\nreport-old\n'
print('CHECK_OK')
