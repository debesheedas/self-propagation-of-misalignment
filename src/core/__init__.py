"""Harness for studying self-propagation of misaligned goals via agentic memory.

Layout:
  config      .env loading + the model registry (alias -> API model id)
  models      thin model-client wrappers (Anthropic / OpenAI-compatible)
  agent       the multi-turn tool-use session loop
  harness     the stateful sandbox (fake filesystem, gates, persistent memory) and its tools
  judges      the LLM judges (banking, concealment, evaluation-awareness)
  banking / stealth / monitor    the three contrastive metrics and the memory auditor
  redteam     the research-question registry (rq_spec), the disposition gate, carrier task banks
  report      the trace store reader and the transcript renderer
"""

__version__ = "1.0.0"
