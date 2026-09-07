def parse(text):
    return dict(line.split('=') for line in text.splitlines())
