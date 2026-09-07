def read_names(text):
 return [s.split(',')[0] for s in text.splitlines()[1:]]
