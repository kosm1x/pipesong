# Import pipesong before any test module imports pipecat: its __init__ sets
# NLTK_DISABLE_IMPORT_SECURITY, without which nltk's CWD-import guard blocks
# pipecat's deps from the in-repo .venv (see src/pipesong/__init__.py).
import pipesong  # noqa: F401
