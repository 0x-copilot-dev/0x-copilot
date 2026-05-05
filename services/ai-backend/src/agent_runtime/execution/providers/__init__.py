"""Provider-specific stream adapters (PR 1.1 follow-up D scaffold).

Each adapter wraps a raw provider stream, intercepts native citation
primitives (Anthropic ``citations_delta``, OpenAI Responses
``output_text.done.annotations``), routes them through the universal
:class:`agent_runtime.capabilities.citations.CitationLedger`, and
substitutes the resulting ``[c<id>]`` token into the corresponding text
output before the rest of the runtime sees the chunk.

The adapters are intentionally provider-agnostic at their boundary: they
only interact with :class:`CitationLedger`, which means the same wire
format and FE rendering code work for tool-emitted, Anthropic-native, and
OpenAI-native citations alike.
"""
