from pathlib import Path
assert Path("alpha.txt").read_text()=="ALPHA\n"
assert Path("beta.txt").read_text()=="BETA\n"
print("DELIVERY_OK")
