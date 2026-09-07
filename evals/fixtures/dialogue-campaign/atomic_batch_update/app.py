def apply_updates(state, updates):
    for key, delta in updates:
        state[key] = state.get(key, 0) + delta
