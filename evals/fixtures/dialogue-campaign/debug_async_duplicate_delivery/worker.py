def handle(msg,db,queue): queue.ack(msg); db.commit(msg)
