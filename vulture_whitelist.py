# vulture whitelist: names that look unused but are used where vulture cannot see it
# (framework hooks, attributes read by reflection, public API for other services).
# One line per name, e.g. `_.on_startup  # FastAPI lifespan hook, called by the framework`,
# each with the reason. Guarded: the tech lead reviews every addition (CODEOWNERS).

# Celery bootsteps (<pkg>/platform/foundry/worker/heartbeat.py): the worker blueprint reads each
# step class's `requires`. An assignment, not a bare `_.name`, so ruff (F821, B018) accepts it.
_ = object.requires
