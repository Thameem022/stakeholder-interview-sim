"""All AI calls go to Amazon Bedrock, direct, from the backend (SEC-AI-001).

  claude.py      rubric scoring — Claude, through the official Anthropic SDK
  embeddings.py  retrieval embeddings — Amazon Titan Text Embeddings V2
  guardrails.py  Bedrock Guardrails on persona speech, student turns, feedback
  aws.py         the one place AWS credentials are resolved

Nothing here ever reaches the browser.
"""
