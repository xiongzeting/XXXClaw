from pagination import page
def list_items(rows,active=None,offset=0,limit=20):
 selected=page(rows,offset,limit)
 return {'total':len(selected),'items':selected}
