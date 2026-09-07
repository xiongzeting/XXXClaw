from app import slug
assert slug("  HELLO   World ")=="hello-world"
assert slug("")==""
print("VERIFIED")
