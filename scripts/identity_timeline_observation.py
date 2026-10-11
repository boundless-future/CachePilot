"""Transparent synchronous call observation for isolated diagnostics."""
def observe_call(target, name, record, describe):
    original = getattr(target, name)
    def wrapped(*args, **kwargs):
        before = describe(args, kwargs)
        record(name + '_before', **before)
        try:
            result = original(*args, **kwargs)
        except Exception as exc:
            record(name + '_error', error=repr(exc), **before)
            raise
        record(name + '_after', **describe(args, kwargs))
        return result
    setattr(target, name, wrapped)
