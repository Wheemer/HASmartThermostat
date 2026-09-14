"""Do not treat a missing or restore-only output as a usable actuator."""


def output_available(state):
    return (state is not None
            and state.state not in ("unknown", "unavailable")
            and not getattr(state, "attributes", {}).get("restored", False))
