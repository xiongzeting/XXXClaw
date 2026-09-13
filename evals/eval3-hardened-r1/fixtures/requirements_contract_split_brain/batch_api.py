from api import respond
def respond_batch(data): return [respond(x) for x in data["items"]]
