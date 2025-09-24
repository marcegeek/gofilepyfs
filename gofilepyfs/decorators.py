import functools


def compose_decorators(*decorators):
    """Compose several decorators as a new decorator.

    The resulting decorator will work as if the decorators were written from top to bottom in the same order.
    """
    def deco(func):
        for dec in reversed(decorators):
            func = dec(func)
        return func

    return deco


def with_defaults(**default_kwargs):
    """Decorator which injects default values to an instance or class method in kwargs.

    When default values are callable, they're evaluated with the passed object.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(obj, *args, **kwargs):
            for key, default in default_kwargs.items():
                # if there's no value, override with default
                if key not in kwargs or kwargs[key] is None:
                    # if default is callable, evaluate with obj (e.g. a lambda which uses some field(s))
                    kwargs[key] = default(obj) if callable(default) else default
            return func(obj, *args, **kwargs)

        return wrapper

    return decorator
