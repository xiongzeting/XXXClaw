import sys,json
from reader import read_names
print('DEBUG')
print(json.dumps(read_names(sys.stdin.read())))
