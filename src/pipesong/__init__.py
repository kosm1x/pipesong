import os

# nltk >= 3.10 (pulled in by pipecat 1.6+) installs an import hook that blocks any
# nltk-initiated import whose module resolves inside the CWD. With the venv inside
# the repo (.venv/ — our layout here AND on the GPU box via runpod_setup.sh), every
# dependency resolves "inside the CWD" and `import pipecat` dies at boot. The hook
# guards against CWD-shadowing attacks, which don't apply to this controlled deploy.
# Must be set before the first pipecat import — a runtime .env load is too late.
os.environ.setdefault("NLTK_DISABLE_IMPORT_SECURITY", "1")
