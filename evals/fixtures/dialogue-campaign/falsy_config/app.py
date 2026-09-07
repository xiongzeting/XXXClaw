def merge(defaults, overrides):
    return {k: overrides.get(k) or v for k, v in defaults.items()}
