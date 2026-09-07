import json,tempfile
from pathlib import Path
from package.transform import transform
assert transform({'schema':1,'services':[{'host':'x','port':1}]})['schema']==2
print('SMOKE_OK')
