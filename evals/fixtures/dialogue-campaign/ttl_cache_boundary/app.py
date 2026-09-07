class Cache:
    def __init__(self): self.items={}
    def set(self,key,value,ttl,now): self.items[key]=(value,now+ttl)
    def get(self,key,now):
        value,expires=self.items[key]
        return value if now<=expires else None
