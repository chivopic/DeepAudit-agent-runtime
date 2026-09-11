"""Sandbox worker: the only component that holds docker.sock (ADR-003 #6).

Imported by the worker process, not by the API. The API reaches it through
``app.services.agent.tools.sandbox_backend``.
"""
