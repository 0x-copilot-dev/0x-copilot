"""One module per runtime storage backend, imported only when selected.

Nothing is imported here on purpose: importing this package must not drag in
any backend's dependencies. ``runtime_adapters.registry`` resolves a provider by
dotted path at call time.
"""
