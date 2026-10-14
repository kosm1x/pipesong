"""MCP server over the Pipesong REST API (roadmap B.1).

Talks to a running API over HTTP only: importing this package must not import
`pipesong.pipeline` or pipecat (no telephony, Kokoro or Deepgram in here).
"""
